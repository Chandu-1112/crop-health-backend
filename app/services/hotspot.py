from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.diagnosis import Diagnosis
from app.models.field import Field
from app.models.farm import Farm


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