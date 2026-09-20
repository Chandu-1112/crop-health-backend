from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.services.alert_service import create_forecast_alert
from app.database.database import get_db

from app.models.field import Field
from app.models.farm import Farm
from app.models.diagnosis import Diagnosis
from app.models.weather import WeatherData
from app.models.user import User

from app.core.dependencies import get_current_user

from app.services.risk_engine import calculate_risk
from app.services.forecast import calculate_forecast_risk
from app.services.weather import get_weather, get_weather_forecast


router = APIRouter(
    prefix="/api/forecast",
    tags=["Risk Forecast"]
)


@router.get("/field/{field_id}")
def get_field_forecast(
    field_id: int,
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 1. Check that the field belongs to the user
    field = (
        db.query(Field)
        .join(Farm)
        .filter(
            Field.id == field_id,
            Farm.user_id == current_user.id
        )
        .first()
    )

    if field is None:
        raise HTTPException(
            status_code=404,
            detail="Field not found"
        )

    # 2. Get latest diagnosis
    diagnosis = (
        db.query(Diagnosis)
        .filter(
            Diagnosis.field_id == field_id
        )
        .order_by(
            Diagnosis.created_at.desc()
        )
        .first()
    )

    if diagnosis is None:
        raise HTTPException(
            status_code=404,
            detail="No diagnosis found for this field"
        )

    # 3. Get latest saved weather
    weather = (
        db.query(WeatherData)
        .filter(
            WeatherData.field_id == field_id
        )
        .order_by(
            WeatherData.created_at.desc()
        )
        .first()
    )

    if weather is None:
        try:
            live_weather = get_weather(
                field.farm.latitude,
                field.farm.longitude,
            )
            weather_values = {
                "temperature": live_weather.get("temperature"),
                "humidity": live_weather.get("humidity"),
                "rainfall": live_weather.get("rainfall"),
            }
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=f"Current weather is unavailable: {str(error)}"
            )
    else:
        weather_values = {
            "temperature": weather.temperature,
            "humidity": weather.humidity,
            "rainfall": weather.rainfall,
        }

    # 4. Calculate current risk
    current_risk = calculate_risk(
        disease=diagnosis.disease,
        confidence=diagnosis.confidence or 0.0,
        severity=diagnosis.severity or "unknown",
        temperature=weather_values["temperature"],
        humidity=weather_values["humidity"],
        rainfall=weather_values["rainfall"],
        growth_stage=field.growth_stage
    )

    # 5. Get actual 14-day weather forecast
    if (
        field.farm.latitude is None
        or field.farm.longitude is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Field location is not available"
        )

    try:
        weather_forecast = get_weather_forecast(
            latitude=field.farm.latitude,
            longitude=field.farm.longitude,
            days=14,
            force_refresh=refresh,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Weather forecast failed: {str(e)}"
        )

    # 6. Calculate future risk
    forecast = calculate_forecast_risk(
        current_risk_score=current_risk["risk_score"],
        forecast=weather_forecast
    )
    forecast_alert = None
    try:
        forecast_alert = create_forecast_alert(
            db=db,
            field_id=field.id,
            crop=field.crop,
            disease=diagnosis.disease,
            forecast=forecast,
            language=current_user.language,
        )
    except Exception:
        # An alert persistence failure must not hide a valid forecast.
        db.rollback()

    return {
    "field_id": field.id,
    "field_name": field.name,
    "crop": field.crop,
    "growth_stage": field.growth_stage,

    "diagnosis": {
        "disease": diagnosis.disease,
        "confidence": diagnosis.confidence,
        "severity": diagnosis.severity
    },

    "current_risk": {
        "score": current_risk["risk_score"],
        "level": current_risk["risk_level"]
    },

    "forecast": forecast,

    "forecast_alert": {
        "id": forecast_alert.id,
        "severity": forecast_alert.severity,
        "title": forecast_alert.title,
        "message": forecast_alert.message
    } if forecast_alert else None
}