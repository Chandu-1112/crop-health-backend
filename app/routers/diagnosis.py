from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    UploadFile,
    File,
    Form
)
from fastapi.responses import Response
from typing import List
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.diagnosis import Diagnosis
from app.models.field import Field
from app.models.farm import Farm
from app.models.user import User
from app.schemas.diagnosis import DiagnosisResponse
from app.core.dependencies import get_current_user
from app.services.disease import analyze_crop_image


router = APIRouter(
    prefix="/api/diagnosis",
    tags=["Diagnosis"]
)

@router.get("/field/{field_id}/latest")
def get_latest_field_diagnosis(
    field_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    diagnosis = (
        db.query(Diagnosis)
        .join(Field)
        .join(Farm)
        .filter(
            Field.id == field_id,
            Farm.user_id == current_user.id,
        )
        .order_by(Diagnosis.created_at.desc())
        .first()
    )

    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No diagnosis found for this field",
        )

    return {
        "disease": diagnosis.disease,
        "confidence": diagnosis.confidence,
        "severity": diagnosis.severity,
    }


@router.post(
    "/analyze",
    response_model=DiagnosisResponse,
    status_code=status.HTTP_201_CREATED
)
def analyze_crop(
    field_id: int = Form(...),
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Check field ownership
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Field not found"
        )

    # Validate image
    if (
        not image.content_type
        or not image.content_type.startswith("image/")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload a valid image"
        )

    # Read image
    image_bytes = image.file.read()

    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty"
        )

    # Analyze image with Gemini
    try:
        ai_result = analyze_crop_image(
            image_bytes=image_bytes,
            crop=field.crop,
            mime_type=image.content_type,
            language=current_user.language
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI analysis failed: {str(e)}"
        )

    # Save diagnosis and image in PostgreSQL
    diagnosis = Diagnosis(
        field_id=field_id,
        image_data=image_bytes,
        image_type=image.content_type,
        disease=ai_result.get(
            "disease",
            "Unable to determine"
        ),
        confidence=ai_result.get(
            "confidence",
            0.0
        ),
        severity=ai_result.get(
            "severity",
            "unknown"
        ),
        affected_area=ai_result.get(
            "affected_area"
        ),
        diagnosis_source="gemini",
        status="completed",
        explanation=ai_result.get(
            "explanation",
            ""
        )
    )

    db.add(diagnosis)
    db.commit()
    db.refresh(diagnosis)

    return diagnosis


@router.get(
    "/{diagnosis_id}",
    response_model=DiagnosisResponse
)
def get_diagnosis(
    diagnosis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    diagnosis = (
        db.query(Diagnosis)
        .join(Field)
        .join(Farm)
        .filter(
            Diagnosis.id == diagnosis_id,
            Farm.user_id == current_user.id
        )
        .first()
    )

    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnosis not found"
        )

    return diagnosis


@router.get(
    "/{diagnosis_id}/image"
)
def get_diagnosis_image(
    diagnosis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    diagnosis = (
        db.query(Diagnosis)
        .join(Field)
        .join(Farm)
        .filter(
            Diagnosis.id == diagnosis_id,
            Farm.user_id == current_user.id
        )
        .first()
    )

    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnosis not found"
        )

    if not diagnosis.image_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    return Response(
        content=diagnosis.image_data,
        media_type=diagnosis.image_type or "image/jpeg"
    )


@router.get(
    "/field/{field_id}",
    response_model=List[DiagnosisResponse]
)
def get_field_diagnoses(
    field_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Check field ownership
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Field not found"
        )

    diagnoses = (
        db.query(Diagnosis)
        .filter(
            Diagnosis.field_id == field_id
        )
        .order_by(
            Diagnosis.created_at.desc()
        )
        .all()
    )

    return diagnoses