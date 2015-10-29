# Copyright (c) 2015, The MITRE Corporation. All rights reserved.
# See LICENSE.txt for complete terms.
"""
Entity field data descriptors (TypedFields) and associated classes.
"""
import collections
import importlib
import functools

from .datautils import is_sequence
from .dates import parse_date, parse_datetime
from .xml import strip_cdata
from .vendor import six
from .compat import long


def unset(entity, *types):
    """Unset the TypedFields on the input `entity`.

    Args:
        entity: A mixbox.Entity object.
        *types: A variable-length list of TypedField subclasses. If not
            provided, defaults to TypedField.
    """
    if not types:
        types = [TypedField]

    fields = list(entity._fields.keys())
    remove = (x for x in fields if isinstance(x, types))

    for field in remove:
        del entity._fields[field]


def _matches(field, params):
    for key, value in six.iteritems(params):
        if not hasattr(field, key):
            return False
        if getattr(field, key) != value:
            return False
    return True


def find(entity, **kwargs):
    """Find a TypedField.  The **kwargs are TypedField __init__ kwargs.

    Note:
        TypedFields.__init__() can accept a string or a class as a type_
        argument, but this method expects a class.

    Args:
        **kwargs: TypedField __init__ **kwargs to search on.

    Returns:
        A list of TypedFields with matching **kwarg values.
    """
    if not hasattr(entity, "typed_fields"):
        return []

    # Some **kwargs get remapped to TypedField internal vars.
    kwargmap = {
        "factory": "_factory",
        "key_name": "_key_name"
    }

    params = {}
    for param, value in six.iteritems(kwargs):
        key = kwargmap.get(param, param)
        params[key] = value

    fields = [x for x in entity.typed_fields if _matches(x, params)]

    return fields


def _import_class(classpath):
    """Import the class referred to by the fully qualified class path.

    Args:
        classpath: A full "A.B.CLASSNAME" package path to a class definition.

    Returns:
        The class referred to by the classpath.

    Raises:
        ImportError: If an error occurs while importing the module.
        AttributeError: IF the class does not exist in the imported module.
    """
    modname, classname = classpath.rsplit(".", 1)
    module = importlib.import_module(modname)
    klass  = getattr(module, classname)
    return klass


def _resolve_class(classref):
    if classref is None:
        return None
    elif isinstance(classref, six.class_types):
        return classref
    elif isinstance(classref, six.string_types):
        return _import_class(classref)
    else:
        raise ValueError("Unable to resolve class for '%s'" % classref)


class _TypedList(collections.MutableSequence):
    def __init__(self, type, *args):
        self._inner = []
        self._type  = type

        for item in args:
            if is_sequence(item):
                self.extend(item)
            else:
                self.append(item)

    def _is_valid(self, value):
        if hasattr(self._type, "istypeof"):
            return self._type.istypeof(value)
        else:
            return isinstance(value, self._type)

    def _fix_value(self, value):
        """Attempt to coerce value into the correct type.

        Subclasses can override this function.
        """
        try:
            new_value = self._type(value)
        except:
            error = "Can't put '{0}' ({1}) into a {2}. Expected a {3} object."
            error = error.format(
                value,                  # Input value
                type(value),            # Type of input value
                type(self),             # Type of collection
                self._type    # Expected type of input value
            )
            raise ValueError(error)

        return new_value

    def __nonzero__(self):
        return bool(self._inner)

    def __getitem__(self, key):
        return self._inner.__getitem__(key)

    def __setitem__(self, key, value):
        if not self._is_valid(value):
            value = self._fix_value(value)
        self._inner.__setitem__(key, value)

    def __delitem__(self, key):
        self._inner.__delitem__(key)

    def __len__(self):
        return len(self._inner)

    def insert(self, idx, value):
        if not value:
            return
        if not self._is_valid(value):
            value = self._fix_value(value)
        self._inner.insert(idx, value)

    def __repr__(self):
        return self._inner.__repr__()

    def __str__(self):
        return self._inner.__str__()


class TypedField(object):

    def __init__(self, name, type_=None,
                 key_name=None, comparable=True, multiple=False,
                 preset_hook=None, postset_hook=None, factory=None):
        """
        Create a new field.

        Args:
            `name` (str): name of the field as contained in the binding class.
            `type_` (type/str): Required type for values assigned to this field.
                If`None`, no type checking is performed. String values are
                treated as fully qualified package paths to a class (e.g.,
                "A.B.C" would be the full path to the type "C".)
            `key_name` (str): name for field when represented as a dictionary.
                (Optional) If omitted, `name.lower()` will be used.
            `comparable` (boolean): whether this field should be considered
                when checking Entities for equality. Default is True. If False,
                this field is not considered.
            `multiple` (boolean): Whether multiple instances of this field can
                exist on the Entity.
            `preset_hook` (callable): called before assigning a value to this
                field, but after type checking is performed (if applicable).
                This should typically be used to perform additional validation
                checks on the value, perhaps based on current state of the
                instance. The callable should accept two arguments: (1) the
                instance object being modified, and (2)the value it is being
                set to.
            `postset_hook` (callable): similar to `preset_hook` (and takes the
                same arguments), but is called after setting the value. This
                can be used, for example, to modify other fields of the
                instance to maintain some type of invariant.
        """
        self.name = name
        self._type = type_
        self._key_name = key_name
        self.comparable = comparable
        self.multiple = multiple
        self.preset_hook = preset_hook
        self.postset_hook = postset_hook
        self.is_type_castable  = getattr(type_, "_try_cast", False)
        self._factory = factory

        if type_:
            self.listclass = functools.partial(_TypedList, type_)
        else:
            self.listclass = list

    def __get__(self, instance, owner=None):
        """Return the TypedField value for the input `instance` and `owner`.

        If the TypedField is a "multiple" field and hasn't been set yet,
        set the field to an empty list and return it.

        Args:
            instance: An instance of the `owner` class that this TypedField
                belongs to..
            owner: The TypedField owner class.
        """
        if instance is None:
            return self
        elif self in instance._fields:
            return instance._fields[self]
        elif self.multiple:
            return instance._fields.setdefault(self, [])
        else:
            return None

    def _clean(self, value):
        """Validate and clean a candidate value for this field."""
        if value is None:
            return None
        elif self.type_ is None:
            return value
        elif self.check_type(value):
            return value
        elif self.is_type_castable:  # noqa
            return self.type_(value)

        error_fmt = "%s must be a %s, not a %s"
        error = error_fmt % (self.name, self.type_, type(value))
        raise TypeError(error)

    def __set__(self, instance, value):
        """Sets the field value on `instance` for this TypedField.

        If the TypedField has a `type_` and `value` is not an instance of
        ``type_``, an attempt may be made to convert `value` into an instance
        of ``type_``.

        If the field is ``multiple``, an attempt is made to convert `value`
        into a list if it is not an iterable type.
        """
        if self.multiple:
            if value is None:
                value = self.listclass()
            elif not is_sequence(value):
                value = self.listclass([self._clean(value)])
            else:
                value = self.listclass(self._clean(x) for x in value if x is not None)
        else:
            value = self._clean(value)

        if self.preset_hook:
            self.preset_hook(instance, value)

        instance._fields[self] = value

        if self.postset_hook:
            self.postset_hook(instance, value)

    def __str__(self):
        return self.name

    def check_type(self, value):
        if not self.type_:
            return True
        elif hasattr(self.type_, "istypeof"):
            return self.type_.istypeof(value)
        else:
            return isinstance(value, self.type_)

    @property
    def key_name(self):
        if self._key_name:
            return self._key_name
        else:
            return self.name.lower()

    @property
    def type_(self):
        self._type = _resolve_class(self._type)
        return self._type

    @type_.setter
    def type_(self, value):
        self._type = value

    @property
    def factory(self):
        self._factory = _resolve_class(self._factory)
        return self._factory

    @factory.setter
    def factory(self, value):
        self._factory = value

    @property
    def transformer(self):
        """Return the class for this field that transforms non-Entity objects
        (e.g., dicts or binding objects) into Entity instances.

        Any non-None value returned from this method should implement a
        from_obj() and from_dict() method.

        Returns:
            None if no type_ or factory is defined by the field. Return a class
            with from_dict and from_obj methods otherwise.
        """
        if self.factory:
            return self.factory
        elif self.type_:
            return self.type_
        else:
            return None


class BytesField(TypedField):
    def _clean(self, value):
        return six.binary_type(value)


class TextField(TypedField):
    def _clean(self, value):
        return six.text_type(value)


class BooleanField(TypedField):
    def _clean(self, value):
        return bool(value)


class IntegerField(TypedField):
    def _clean(self, value):
        if value in (None, ""):
            return None
        elif isinstance(value, six.string_types):
            return int(value, 0)
        else:
            return int(value)


class LongField(TypedField):
    def _clean(self, value):
        if value in (None, ""):
            return None
        elif isinstance(value, six.string_types):
            return long(value, 0)
        else:
            return long(value)


class FloatField(TypedField):
    def _clean(self, value):
        if value not in (None, ""):
            return float(value)


class DateTimeField(TypedField):
    def _clean(self, value):
        return parse_datetime(value)


class DateField(TypedField):
    def _clean(self, value):
        return parse_date(value)


class CDATAField(TypedField):
    def _clean(self, value):
        return strip_cdata(value)


class IdField(TypedField):
    def __set__(self, instance, value):
        """Set the id field to `value`. If `value` is not None or an empty
        string, unset the idref fields on `instance`.
        """
        super(IdField, self).__set__(instance, value)

        if value:
            unset(instance, IdrefField)


class IdrefField(TypedField):
    def __set__(self, instance, value):
        """Set the idref field to `value`. If `value` is not None or an empty
        string, unset the id fields on `instance`.
        """
        super(IdrefField, self).__set__(instance, value)

        if value:
            unset(instance, IdField)
