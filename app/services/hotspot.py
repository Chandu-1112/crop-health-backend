from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.alert import Alert
from app.models.diagnosis import Diagnosis
from app.models.field import Field
from app.models.farm import Farm
from app.models.user import User


HOTSPOT_RADIUS_KM = 5.0
HOTSPOT_MINIMUM_CASES = 2
HOTSPOT_DAYS = 14


def _distance_km(
    first_latitude: float,
    first_longitude: float,
    second_latitude: float,
    second_longitude: float,
) -> float:
    earth_radius_km = 6371.0
    latitude_delta = radians(second_latitude - first_latitude)
    longitude_delta = radians(second_longitude - first_longitude)
    first_latitude = radians(first_latitude)
    second_latitude = radians(second_latitude)

    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(first_latitude)
        * cos(second_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return earth_radius_km * 2 * asin(sqrt(haversine))


def create_hotspot_alerts(
    db: Session,
    diagnosis: Diagnosis,
    source_farm: Farm,
    radius_km: float = HOTSPOT_RADIUS_KM,
    minimum_cases: int = HOTSPOT_MINIMUM_CASES,
    days: int = HOTSPOT_DAYS,
) -> int:
    """Notify fields on nearby farms when a disease reaches hotspot levels."""
    if (
        not diagnosis.disease
        or diagnosis.confidence is None
        or diagnosis.confidence < 0.60
        or source_farm.latitude is None
        or source_farm.longitude is None
    ):
        return 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    matching_cases = (
        db.query(Diagnosis, Farm)
        .join(Field, Diagnosis.field_id == Field.id)
        .join(Farm, Field.farm_id == Farm.id)
        .filter(
            func.lower(Diagnosis.disease) == diagnosis.disease.lower(),
            Diagnosis.created_at >= cutoff_date,
            Diagnosis.confidence >= 0.60,
        )
        .all()
    )

    nearby_farms = {}
    nearby_case_count = 0
    for matching_diagnosis, farm in matching_cases:
        if farm.latitude is None or farm.longitude is None:
            continue

        distance = _distance_km(
            source_farm.latitude,
            source_farm.longitude,
            farm.latitude,
            farm.longitude,
        )
        if distance <= radius_km:
            nearby_farms[farm.id] = distance
            nearby_case_count += 1

    if (
        nearby_case_count < minimum_cases
        or source_farm.id not in nearby_farms
    ):
        return 0

    recipient_farms = [
        farm_id
        for farm_id in nearby_farms
        if farm_id != source_farm.id
    ]
    if not recipient_farms:
        return 0

    recipient_fields = (
        db.query(Field, User.language)
        .join(Farm, Field.farm_id == Farm.id)
        .join(User, Farm.user_id == User.id)
        .filter(Field.farm_id.in_(recipient_farms))
        .all()
    )

    created_count = 0
    for field, language in recipient_fields:
        labels = {
            "en": (
                "Nearby Disease Hotspot",
                "A nearby farm has multiple confirmed cases of",
                "Inspect your crop and take preventive action.",
            ),
            "te": (
                "సమీపంలోని వ్యాధి హాట్‌స్పాట్",
                "సమీపంలోని పొలాల్లో అనేక నిర్ధారిత కేసులు ఉన్నాయి:",
                "మీ పంటను పరిశీలించి నివారణ చర్యలు తీసుకోండి.",
            ),
            "hi": (
                "पास में रोग हॉटस्पॉट",
                "पास के खेतों में इसके कई पुष्ट मामले हैं:",
                "अपनी फसल की जांच करें और रोकथाम की कार्रवाई करें।",
            ),
        }.get(language, None) or (
            "Nearby Disease Hotspot",
            "A nearby farm has multiple confirmed cases of",
            "Inspect your crop and take preventive action.",
        )
        message = (
            f"{labels[1]} {diagnosis.disease}. "
            f"{labels[2]} "
            f"Hotspot radius: {radius_km:g} km; "
            f"time window: {days} days."
        )

        existing_alert = (
            db.query(Alert)
            .filter(
                Alert.field_id == field.id,
                Alert.type == "hotspot_risk",
                Alert.message == message,
            )
            .first()
        )
        if existing_alert:
            continue

        db.add(
            Alert(
                field_id=field.id,
                type="hotspot_risk",
                severity="high",
                title=labels[0],
                message=message,
                is_read=False,
            )
        )
        created_count += 1

    if created_count:
        db.commit()

    return created_count


def detect_hotspots(
    db: Session,
    disease: str,
    minimum_cases: int = 3,
    days: int = 14
):
    cutoff_date = datetime.now(timezone.utc) - timedelta(
        days=days
    )

    results = (
        db.query(
            Farm.id.label("farm_id"),
            Farm.name.label("farm_name"),
            Farm.latitude.label("latitude"),
            Farm.longitude.label("longitude"),
            func.count(Diagnosis.id).label("case_count")
        )
        .join(
            Field,
            Field.farm_id == Farm.id
        )
        .join(
            Diagnosis,
            Diagnosis.field_id == Field.id
        )
        .filter(
            Diagnosis.disease == disease,
            Diagnosis.created_at >= cutoff_date,
            Diagnosis.confidence >= 0.60
        )
        .group_by(
            Farm.id,
            Farm.name,
            Farm.latitude,
            Farm.longitude
        )
        .having(
            func.count(Diagnosis.id) >= minimum_cases
        )
        .all()
    )

    hotspots = []

    for result in results:
        hotspots.append({
            "farm_id": result.farm_id,
            "farm_name": result.farm_name,
            "latitude": result.latitude,
            "longitude": result.longitude,
            "case_count": result.case_count,
            "time_window_days": days
        })

    return hotspots