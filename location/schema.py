import graphene_django_optimizer as gql_optimizer

from core.models import Officer
from core.schema import OrderedDjangoFilterConnectionField
from core.schema import signal_mutation_module_validate
from django.utils.translation import gettext as _
from django.core.exceptions import PermissionDenied
from graphene_django.filter import DjangoFilterConnectionField
from location.gql_mutations import (
    CreateHealthFacilityMutation,
    DeleteHealthFacilityMutation,
    UpdateHealthFacilityMutation,
    CreateLocationMutation,
    UpdateLocationMutation,
    DeleteLocationMutation,
    MoveLocationMutation,
    CreateHealthFacilityContractMutation,
    UpdateHealthFacilityContractMutation,
)
from location.gql_queries import (
    UserDistrictGQLType,
    LocationGQLType,
    HealthFacilityGQLType,
    HealthFacilityContractGQLType,
)
from location.models import (
    HealthFacility,
    Location,
    LocationManager,
    UserDistrict,
    LocationMutation,
    HealthFacilityMutation,
    HealthFacilityContract,
)
from location.services import LocationService, HealthFacilityService
from location.apps import LocationConfig
import graphene
from django.db.models import Q
from core.utils import filter_validity
from core import models as core_models
from django.conf import settings


class Query(graphene.ObjectType):
    health_facilities = OrderedDjangoFilterConnectionField(
        HealthFacilityGQLType,
        showHistory=graphene.Boolean(),
        orderBy=graphene.List(of_type=graphene.String),
    )
    locations = OrderedDjangoFilterConnectionField(
        LocationGQLType,
        orderBy=graphene.List(of_type=graphene.String),
    )
    locations_all = OrderedDjangoFilterConnectionField(
        LocationGQLType, orderBy=graphene.List(of_type=graphene.String)
    )
    locations_str = DjangoFilterConnectionField(
        LocationGQLType,
        str=graphene.String(),
    )
    user_districts = graphene.List(UserDistrictGQLType)
    officer_locations = graphene.List(
        LocationGQLType,
        officer_code=graphene.String(required=True),
        location_type=graphene.String(required=False),
        description="Returns list of locations assigned to a given enrolment officer.",
    )
    health_facilities_str = DjangoFilterConnectionField(
        HealthFacilityGQLType,
        str=graphene.String(),
        region_uuid=graphene.String(),
        district_uuid=graphene.String(),
        districts_uuids=graphene.List(of_type=graphene.String),
        ignore_location=graphene.Boolean(),
    )
    validate_location_code = graphene.Field(
        graphene.Boolean,
        location_code=graphene.String(required=True),
        description="Checks that the specified location code is unique.",
    )
    validate_health_facility_code = graphene.Field(
        graphene.Boolean,
        health_facility_code=graphene.String(required=True),
        description="Checks that the specified health facility code is unique.",
    )
    active_contracted_health_facilities = graphene.List(
        HealthFacilityGQLType,
        location_id=graphene.Int(required=True),
        on_date=graphene.Date(required=False),
        description="HF with an active contract for a location at given date (default today)",
    )
    expired_contracted_health_facilities = graphene.List(
        HealthFacilityGQLType,
        location_id=graphene.Int(required=True),
        on_date=graphene.Date(required=False),
        description="HF with a contract that expired before given date (default today)",
    )
    active_contracted_locations = graphene.List(
        LocationGQLType,
        health_facility_id=graphene.Int(required=True),
        on_date=graphene.Date(required=False),
        description="Locations with an active contract for the given HF at the given date (default today)",
    )
    expired_contracted_locations = graphene.List(
        LocationGQLType,
        health_facility_id=graphene.Int(required=True),
        on_date=graphene.Date(required=False),
        description="Locations with a contract that is not active for the given HF at the given date (default today)",
    )
    health_facility_contracts = OrderedDjangoFilterConnectionField(
        HealthFacilityContractGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        description="Get all health facility-location contracts",
    )
    health_facility_contracts_by_location = graphene.List(
        HealthFacilityContractGQLType,
        location_id=graphene.Int(required=True),
        description="Get all health facility contracts for a specific location",
    )
    health_facility_contract = graphene.Field(
        HealthFacilityContractGQLType,
        contract_id=graphene.Int(required=True),
        description="Get a single health facility contract by contract ID",
    )
    health_facility_uuid = graphene.Field(
        graphene.String,
        hf_id=graphene.Int(required=True),
        description="Get health facility UUID by health facility ID",
    )
    health_facility_by_id = graphene.Field(
        HealthFacilityGQLType,
        hf_id=graphene.Int(required=True),
        description="Get complete health facility information by health facility ID",
    )

    def resolve_health_facilities(self, info, **kwargs):
        show_history = kwargs.get("showHistory", False) and info.context.user.has_perms(
            LocationConfig.gql_query_health_facilities_perms
        )
        # OMT-281 allow anyone to query, limited by the get_queryset
        # if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        query = HealthFacility.get_queryset(None, info.context.user, **kwargs)
        if not show_history:
            query = HealthFacility.filter_queryset(query)

        query = LocationManager().build_user_location_filter_query(
            info.context.user._u, queryset=query
        )

        return gql_optimizer.query(query.all(), info)

    def resolve_validate_location_code(self, info, **kwargs):
        if not info.context.user.has_perms(LocationConfig.gql_query_locations_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = LocationService.check_unique_code(code=kwargs["location_code"])
        return False if errors else True

    def resolve_active_contracted_health_facilities(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        location_id = kwargs.get("location_id")
        on_date = kwargs.get("on_date")
        # Enforce row security on the requested location
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            if not Location.objects.is_allowed(info.context.user._u, [location_id]):
                raise PermissionDenied(_("unauthorized"))
        qs = HealthFacilityContract.active_health_facilities_for_location(location_id, on_date)
        return gql_optimizer.query(qs, info)

    def resolve_expired_contracted_health_facilities(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        location_id = kwargs.get("location_id")
        on_date = kwargs.get("on_date")
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            if not Location.objects.is_allowed(info.context.user._u, [location_id]):
                raise PermissionDenied(_("unauthorized"))
        qs = HealthFacilityContract.expired_health_facilities_for_location(location_id, on_date)
        return gql_optimizer.query(qs, info)

    def resolve_active_contracted_locations(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        hf_id = kwargs.get("health_facility_id")
        on_date = kwargs.get("on_date")
        # Enforce row security based on the HF location
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            try:
                hf = HealthFacility.objects.filter(validity_to__isnull=True).only("location_id").get(id=hf_id)
            except HealthFacility.DoesNotExist:
                return Location.objects.none()
            if not Location.objects.is_allowed(info.context.user._u, [hf.location_id]):
                raise PermissionDenied(_("unauthorized"))
        qs = HealthFacilityContract.active_locations_for_health_facility(hf_id, on_date)
        return gql_optimizer.query(qs, info)

    def resolve_expired_contracted_locations(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        hf_id = kwargs.get("health_facility_id")
        on_date = kwargs.get("on_date")
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            try:
                hf = HealthFacility.objects.filter(validity_to__isnull=True).only("location_id").get(id=hf_id)
            except HealthFacility.DoesNotExist:
                return Location.objects.none()
            if not Location.objects.is_allowed(info.context.user._u, [hf.location_id]):
                raise PermissionDenied(_("unauthorized"))
        qs = HealthFacilityContract.expired_locations_for_health_facility(hf_id, on_date)
        return gql_optimizer.query(qs, info)

    def resolve_validate_health_facility_code(self, info, **kwargs):
        if not info.context.user.has_perms(
            LocationConfig.gql_query_health_facilities_perms
        ):
            raise PermissionDenied(_("unauthorized"))
        errors = HealthFacilityService.check_unique_code(
            code=kwargs["health_facility_code"]
        )
        return False if errors else True

    def resolve_locations(self, info, **kwargs):
        # OMT-281 allow querying to anyone, with limitations in the get_queryset
        # if not info.context.user.has_perms(LocationConfig.gql_query_locations_perms):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))

    def resolve_locations_all(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        return Location.objects.filter(*filter_validity()).all()

    def resolve_locations_str(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))

        queryset = Location.get_queryset(None, info.context.user)
        filters = [*filter_validity(**kwargs)]

        str = kwargs.get("str")
        if str is not None:
            filters += [Q(code__icontains=str) | Q(name__icontains=str)]

        return queryset.filter(*filters)

    def resolve_health_facilities_str(self, info, **kwargs):
        if not info.context.user.is_authenticated:
            raise PermissionDenied(_("unauthorized"))
        filters = [*filter_validity(**kwargs)]
        search = kwargs.get("str")
        district_uuid = kwargs.get("district_uuid")
        district_uuids = kwargs.get("districts_uuids")
        region_uuid = kwargs.get("region_uuid")
        if search is not None:
            filters += [Q(code__icontains=search) | Q(name__icontains=search)]
        if district_uuid is not None:
            filters += [Q(location__uuid=district_uuid)]
        if district_uuids is not None:
            if None not in district_uuids:
                filters += [Q(location__uuid__in=district_uuids)]
        if region_uuid is not None:
            filters += [Q(location__parent__uuid=region_uuid)]

        if kwargs.get("ignore_location", False) is False:
            if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
                filters += [
                    LocationManager().build_user_location_filter_query(
                        info.context.user._u, loc_types=["D"]
                    )
                ]
        return HealthFacility.objects.filter(*filters)

    def resolve_user_districts(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not isinstance(info.context.user._u, core_models.InteractiveUser):
            raise NotImplementedError(
                "Only Interactive Users are registered for districts"
            )
        return [
            UserDistrictGQLType(d)
            for d in UserDistrict.get_user_districts(info.context.user._u)
        ]

    def resolve_officer_locations(self, info, **kwargs):
        if not info.context.user.has_perms(LocationConfig.gql_query_locations_perms):
            raise PermissionDenied(_("unauthorized"))
        current_officer = Officer.objects.get(
            code=kwargs["officer_code"], validity_to__isnull=True
        )
        if "location_type" in kwargs:
            return current_officer.officer_allowed_locations.filter(
                type=kwargs["location_type"]
            )
        return current_officer.officer_allowed_locations

    def resolve_health_facility_contracts(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
            raise PermissionDenied(_("unauthorized"))
        
        query = HealthFacilityContract.objects.all()
        
        # Apply row security if enabled
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            query = LocationManager().build_user_location_filter_query(
                info.context.user._u, queryset=query, prefix="location"
            )
        
        return gql_optimizer.query(query, info)

    def resolve_health_facility_contracts_by_location(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
            raise PermissionDenied(_("unauthorized"))
        
        location_id = kwargs.get("location_id")
        
        # Enforce row security on the requested location
        if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
            if not Location.objects.is_allowed(info.context.user._u, [location_id]):
                raise PermissionDenied(_("unauthorized"))
        
        query = HealthFacilityContract.objects.filter(location_id=location_id)
        return gql_optimizer.query(query, info)

    def resolve_health_facility_contract(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
            raise PermissionDenied(_("unauthorized"))
        
        contract_id = kwargs.get("contract_id")
        
        try:
            contract = HealthFacilityContract.objects.get(id=contract_id)
            
            # Enforce row security on the contract's location
            if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
                if not Location.objects.is_allowed(info.context.user._u, [contract.location_id]):
                    raise PermissionDenied(_("unauthorized"))
            
            return contract
        except HealthFacilityContract.DoesNotExist:
            return None

    def resolve_health_facility_uuid(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
            raise PermissionDenied(_("unauthorized"))
        
        hf_id = kwargs.get("hf_id")
        
        try:
            health_facility = HealthFacility.objects.get(id=hf_id)
            
            # Enforce row security on the health facility's location
            if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
                if not Location.objects.is_allowed(info.context.user._u, [health_facility.location_id]):
                    raise PermissionDenied(_("unauthorized"))
            
            return health_facility.uuid
        except HealthFacility.DoesNotExist:
            return None

    def resolve_health_facility_by_id(self, info, **kwargs):
        if info.context.user.is_anonymous:
            raise PermissionDenied(_("unauthorized"))
        if not info.context.user.has_perms(LocationConfig.gql_query_health_facilities_perms):
            raise PermissionDenied(_("unauthorized"))
        
        hf_id = kwargs.get("hf_id")
        
        try:
            health_facility = HealthFacility.objects.select_related(
                'location', 'location__parent', 'legal_form', 'sub_level',
                'services_pricelist', 'items_pricelist'
            ).get(id=hf_id)
            
            # Enforce row security on the health facility's location
            if settings.ROW_SECURITY and not info.context.user._u.is_superuser:
                if not Location.objects.is_allowed(info.context.user._u, [health_facility.location_id]):
                    raise PermissionDenied(_("unauthorized"))
            
            return health_facility
        except HealthFacility.DoesNotExist:
            return None


class Mutation(graphene.ObjectType):
    create_location = CreateLocationMutation.Field()
    update_location = UpdateLocationMutation.Field()
    delete_location = DeleteLocationMutation.Field()
    move_location = MoveLocationMutation.Field()
    create_health_facility = CreateHealthFacilityMutation.Field()
    update_health_facility = UpdateHealthFacilityMutation.Field()
    delete_health_facility = DeleteHealthFacilityMutation.Field()
    create_health_facility_contract = CreateHealthFacilityContractMutation.Field()
    update_health_facility_contract = UpdateHealthFacilityContractMutation.Field()


def on_location_mutation(sender, **kwargs):
    uuid = kwargs["data"].get("uuid", None)
    if not uuid:
        return []
    if "Location" in str(sender._mutation_class):
        impacted_location = Location.objects.get(uuid=uuid)
        LocationMutation.objects.create(
            location=impacted_location, mutation_id=kwargs["mutation_log_id"]
        )
    if "HealthFacility" in str(sender._mutation_class):
        impacted_health_facility = HealthFacility.objects.get(uuid=uuid)
        HealthFacilityMutation.objects.create(
            health_facility=impacted_health_facility,
            mutation_id=kwargs["mutation_log_id"],
        )
    return []


def bind_signals():
    signal_mutation_module_validate["location"].connect(on_location_mutation)
