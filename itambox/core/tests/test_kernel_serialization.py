"""Unit contracts for the kernel serialization leaf (issue #100)."""

import datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import FileField
from django.db.models.fields.files import FieldFile
from django.test import SimpleTestCase

from core.serialization import serialize_object


class _Field:
    __slots__ = ("name", "concrete", "is_relation")

    def __init__(self, name, *, concrete=True, relation=False):
        self.name = name
        self.concrete = concrete
        self.is_relation = relation


class _M2MValues:
    def __init__(self, pks):
        self._pks = pks

    def all(self):
        return self

    def values_list(self, *args, **kwargs):
        return list(self._pks)


class _PlainM2M:
    """A many-to-many value without a queryset-like ``all()``."""

    def __init__(self, pks):
        self._pks = pks

    def __bool__(self):
        return bool(self._pks)


class _BrokenM2M(_M2MValues):
    def values_list(self, *args, **kwargs):
        raise RuntimeError("query failed")


class _Meta:
    def __init__(self, fields, *, m2m=(), pk_name="id"):
        self._fields = fields
        self.many_to_many = list(m2m)
        self.pk = _Field(pk_name)

    def get_fields(self):
        return self._fields


class _Related:
    __slots__ = ("pk",)

    def __init__(self, pk):
        self.pk = pk


class _Obj:
    def __init__(self, meta, **attrs):
        self._meta = meta
        self.__dict__.update(attrs)


class KernelSerializationTests(SimpleTestCase):
    def test_none_object_returns_none(self):
        self.assertIsNone(serialize_object(None))

    def test_scalar_values_are_json_safe(self):
        fields = [
            _Field("id"),
            _Field("name"),
            _Field("amount"),
            _Field("uid"),
            _Field("day"),
            _Field("moment"),
            _Field("clock"),
        ]
        obj = _Obj(
            _Meta(fields),
            id=1,
            name="asset",
            amount=Decimal("12.50"),
            uid=UUID("12345678-1234-5678-1234-567812345678"),
            day=datetime.date(2026, 8, 13),
            moment=datetime.datetime(2026, 8, 13, 12, 30, 45),
            clock=datetime.time(12, 30),
        )
        data = serialize_object(obj)
        self.assertEqual(data["name"], "asset")
        self.assertEqual(data["amount"], "12.50")
        self.assertEqual(data["uid"], "12345678-1234-5678-1234-567812345678")
        self.assertEqual(data["day"], "2026-08-13")
        self.assertEqual(data["moment"], "2026-08-13T12:30:45")
        self.assertEqual(data["clock"], "12:30:00")

    def test_file_field_stores_name_and_empty_file_stores_none(self):
        fields = [_Field("id"), _Field("logo"), _Field("attachment")]
        file_field = FileField()
        obj = _Obj(
            _Meta(fields),
            id=1,
            logo=FieldFile(instance=None, field=file_field, name="logos/a.png"),
            attachment=FieldFile(instance=None, field=file_field, name=None),
        )
        data = serialize_object(obj)
        self.assertEqual(data["logo"], "logos/a.png")
        self.assertIsNone(data["attachment"])

    def test_relation_stores_pk_or_none(self):
        fields = [_Field("id"), _Field("tenant", relation=True), _Field("owner", relation=True)]
        obj = _Obj(_Meta(fields), id=1, tenant=_Related(7), owner=None)
        data = serialize_object(obj)
        self.assertEqual(data["tenant"], 7)
        self.assertIsNone(data["owner"])

    def test_m2m_stores_sorted_pks_and_plain_m2m_stores_empty_list(self):
        fields = [_Field("id"), _Field("tags", concrete=False), _Field("groups", concrete=False)]
        m2m = [_Field("tags"), _Field("groups")]
        obj = _Obj(_Meta(fields, m2m=m2m), id=1, tags=_M2MValues([3, 1, 2]), groups=_PlainM2M([9]))
        data = serialize_object(obj, extra_fields={"tags", "groups"})
        self.assertEqual(data["tags"], [1, 2, 3])
        self.assertEqual(data["groups"], [])

    def test_m2m_query_failure_is_serialized_as_empty_list(self):
        fields = [_Field("id"), _Field("tags", concrete=False)]
        obj = _Obj(_Meta(fields, m2m=[_Field("tags")]), id=1, tags=_BrokenM2M([1]))

        self.assertEqual(serialize_object(obj, extra_fields={"tags"})["tags"], [])

    def test_exclude_and_extra_fields(self):
        fields = [_Field("id"), _Field("secret"), _Field("virtual", concrete=False)]
        obj = _Obj(_Meta(fields), id=1, secret="hidden", virtual="shown")
        data = serialize_object(obj, extra_fields={"virtual"}, exclude_fields={"secret"})
        self.assertNotIn("secret", data)
        self.assertEqual(data["virtual"], "shown")

    def test_missing_attribute_is_skipped(self):
        fields = [_Field("id"), _Field("ghost")]
        obj = _Obj(_Meta(fields), id=1)
        data = serialize_object(obj)
        self.assertNotIn("ghost", data)
