from core.gql.custom_lookup import NotEqual
import graphene
import base64
from graphene_django import DjangoObjectType
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _
from core import prefix_filterset, ExtendedConnection
from location.apps import LocationConfig
from location.models import (
    HealthFacilityLegalForm,
    Location,
    HealthFacilitySubLevel,
    HealthFacilityCatchment,
    HealthFacility,
    UserDistrict,
    OfficerVillage,
    HealthFacilityContract,
)
from django.db.models import Field


class LocationGQLType(DjangoObjectType):
    client_mutation_id = graphene.String()
    bank_account = graphene.String()
    Field.register_lookup(NotEqual)

    def resolve_parent(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        if "location_loader" in info.context.dataloaders and self.parent_id:
            return info.context.dataloaders["location_loader"].load(self.parent_id)
        return self.parent

    class Meta:
        model = Location
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "uuid": ["exact"],
            "code": ["exact", "istartswith", "icontains", "iexact", "ne"],
            "name": ["exact", "istartswith", "icontains", "iexact", "ne"],
            "type": ["exact"],
            "parent__uuid": ["exact", "in"],  # can't import itself!
            "parent__parent__uuid": ["exact", "in"],  # can't import itself!
            # can't import itself!
            "parent__parent__parent__uuid": ["exact", "in"],
            "parent__id": ["exact", "in"],  # can't import itself!
        }

    def resolve_client_mutation_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        location_mutation = (
            self.mutations.select_related("mutation").filter(mutation__status=0).first()
        )
        return (
            location_mutation.mutation.client_mutation_id if location_mutation else None
        )

    def resolve_bank_account(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        # Return the bank account of the first active contracted HF for this location (ordered by code)
        try:
            qs = HealthFacilityContract.active_health_facilities_for_location(self.id)
            hf = qs.order_by("code").first()
            return hf.bank_account if hf else None
        except Exception:
            # Be resilient: never break the query because of this convenience field
            return None

    @classmethod
    def get_queryset(cls, queryset, info):
        if info.field_name == "locationsAll":
            return queryset
        else:
            return Location.get_queryset(queryset, info.context.user)


class HealthFacilityLegalFormGQLType(DjangoObjectType):
    class Meta:
        model = HealthFacilityLegalForm


class HealthFacilitySubLevelGQLType(DjangoObjectType):
    class Meta:
        model = HealthFacilitySubLevel


class HealthFacilityCatchmentGQLType(DjangoObjectType):
    class Meta:
        model = HealthFacilityCatchment


class HealthFacilityContractGQLType(DjangoObjectType):
    class Meta:
        model = HealthFacilityContract

class HealthFacilityGQLType(DjangoObjectType):
    client_mutation_id = graphene.String()
    # Expose region name explicitly as a plain string to avoid leaking Graphene scalar objects
    region = graphene.String()
    # Database PK of the related location (LocationId)
    location_id = graphene.Int(name="locationId")

    class Meta:
        model = HealthFacility
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "uuid": ["exact"],
            "code": ["exact", "istartswith", "icontains", "iexact"],
            "fax": ["exact", "istartswith", "icontains", "iexact", "isnull"],
            "email": ["exact", "istartswith", "icontains", "iexact", "isnull"],
            "name": ["exact", "istartswith", "icontains", "iexact"],
            "level": ["exact"],
            "sub_level": ["exact", "isnull"],
            "care_type": ["exact"],
            "legal_form__code": ["exact"],
            "phone": ["exact", "istartswith", "icontains", "iexact"],
            "status": ["exact"],
            **prefix_filterset("location__", LocationGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection

    def resolve_location(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        if "location_loader" in info.context.dataloaders:
            return info.context.dataloaders["location_loader"].load(self.location_id)

    def resolve_region(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        try:
            # Region is the parent of the district (HF is linked to a district location)
            # Return a native Python string (name) rather than a Graphene scalar
            if self.location and self.location.parent:
                return self.location.parent.name or ""
            return None
        except Exception:
            return None

    def resolve_location_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        # Return the raw FK integer to tblLocations
        return getattr(self, "location_id", None)

    def resolve_catchments(self, info):
        if not info.context.user.has_perms(
            LocationConfig.gql_query_health_facilities_perms
        ):
            raise PermissionDenied(_("unauthorized"))
        return self.catchments.filter(validity_to__isnull=True)

    def resolve_client_mutation_id(self, info):
        if not info.context.user.has_perms(
            LocationConfig.gql_query_health_facilities_perms
        ):
            raise PermissionDenied(_("unauthorized"))
        health_facility_mutation = (
            self.mutations.select_related("mutation").filter(mutation__status=0).first()
        )
        return (
            health_facility_mutation.mutation.client_mutation_id
            if health_facility_mutation
            else None
        )


class UserRegionGQLType(graphene.ObjectType):
    id = graphene.String()
    uuid = graphene.String()
    code = graphene.String()
    name = graphene.String()
    # Database PK of the region (LocationId)
    location_id = graphene.Int(name="locationId")

    # Keep the Django model on a private attribute and resolve fields explicitly
    def __init__(self, region):
        self._region = region

    def resolve_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        if not self._region:
            return None
        return str(base64.b64encode(f"LocationGQLType:{self._region.id}".encode()), "utf-8")

    def resolve_uuid(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._region, "uuid", None)

    def resolve_code(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._region, "code", None)

    def resolve_name(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._region, "name", None)

    def resolve_location_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._region, "id", None)


class UserDistrictGQLType(graphene.ObjectType):
    id = graphene.String()
    uuid = graphene.String()
    code = graphene.String()
    name = graphene.String()
    parent = graphene.Field(UserRegionGQLType)
    # Database PK of the district location (LocationId)
    location_id = graphene.Int(name="locationId")

    # Keep the Django model on a private attribute and resolve fields explicitly
    def __init__(self, district):
        self._district = district

    def resolve_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        if not self._district:
            return None
        return str(
            base64.b64encode(f"LocationGQLType:{self._district.location_id}".encode()),
            "utf-8",
        )

    def resolve_uuid(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._district.location, "uuid", None) if self._district else None

    def resolve_code(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._district.location, "code", None) if self._district else None

    def resolve_name(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._district.location, "name", None) if self._district else None

    def resolve_parent(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        if not self._district:
            return None
        parent = getattr(self._district.location, "parent", None)
        return UserRegionGQLType(parent) if parent else None

    def resolve_location_id(self, info):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        return getattr(self._district, "location_id", None) if self._district else None


class UserDistrictType(DjangoObjectType):
    class Meta:
        model = UserDistrict
        filter_fields = {
            "id": ["exact"],
            "user": ["exact"],
            "location": ["exact"],
        }
        connection_class = ExtendedConnection

    @classmethod
    def get_queryset(cls, queryset, info):
        return UserDistrict.get_queryset(queryset, info)


class OfficerVillageGQLType(DjangoObjectType):
    class Meta:
        model = OfficerVillage

    @classmethod
    def get_queryset(cls, queryset, info):
        return OfficerVillage.get_queryset(queryset, info).filter(
            validity_to__isnull=True
        )
