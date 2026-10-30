"""Schemas for flash sheet and design endpoints."""

from pydantic import BaseModel, Field, model_validator


class FlashSheetResponse(BaseModel):
    id: str
    artist_id: str
    image_url: str
    source_image_url: str
    crop_applied: bool = False
    crop_x: float = 0
    crop_y: float = 0
    crop_w: float = 100
    crop_h: float = 100
    asset_version: int = 1
    title: str | None = None
    status: str
    design_count: int
    created_at: str


class SegmentResult(BaseModel):
    label: str
    score: float
    box: dict  # {"xmin": int, "ymin": int, "xmax": int, "ymax": int}
    role: str = "design"
    context_type: str | None = None


class SegmentResponse(BaseModel):
    sheet_id: str
    image_url: str
    designs: list[SegmentResult]
    context_regions: list[SegmentResult] = Field(default_factory=list)
    design_count: int


class FlashSegmentationRequest(BaseModel):
    """Optional artist-selected sheet region to send through segmentation."""

    crop_x: float = Field(ge=0, lt=100)
    crop_y: float = Field(ge=0, lt=100)
    crop_w: float = Field(gt=0, le=100)
    crop_h: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def crop_must_fit_sheet(self):
        if self.crop_x + self.crop_w > 100 or self.crop_y + self.crop_h > 100:
            raise ValueError("Crop must fit within the source sheet")
        return self


class FlashSheetCropRequest(BaseModel):
    """Persist the usable region of a sheet as its active storefront asset."""

    crop_x: float = Field(ge=0, lt=100)
    crop_y: float = Field(ge=0, lt=100)
    crop_w: float = Field(gt=0, le=100)
    crop_h: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def crop_must_fit_sheet(self):
        if self.crop_x + self.crop_w > 100 or self.crop_y + self.crop_h > 100:
            raise ValueError("Crop must fit within the source sheet")
        return self


class FlashDesignResponse(BaseModel):
    id: str
    sheet_id: str
    artist_id: str
    artist_slug: str | None = None
    artist_name: str | None = None
    crop_x: float
    crop_y: float
    crop_w: float
    crop_h: float
    crop_units: str
    image_url: str | None = None
    preview_url: str | None = None
    source_image_url: str | None = None
    asset_version: int
    context_regions: list[dict] = Field(default_factory=list)
    name: str | None = None
    ai_suggested_name: str | None = None
    price: float | None = None
    estimated_minutes: int | None = None
    size_label: str | None = None
    placement: str | None = None
    style: str | None = None
    deposit_amount: float | None = None
    reclaimable: bool = False
    mask_json: dict | None = None
    canvas_state: dict | None = None
    ai_metadata: dict | None = None
    gala_sync_enabled: bool = False
    status: str
    claimed_by: str | None = None
    claimed_at: str | None = None
    created_at: str


class FlashDesignUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    estimated_minutes: int | None = None
    size_label: str | None = None
    placement: str | None = None
    style: str | None = None
    deposit_amount: float | None = Field(default=None, ge=0)
    reclaimable: bool | None = None
    mask_json: dict | None = None
    canvas_state: dict | None = None
    ai_metadata: dict | None = None
    gala_sync_enabled: bool | None = None
    status: str | None = None  # draft | published | archived


class FlashDesignCreate(BaseModel):
    """Create one design from an artist-selected percentage crop."""

    crop_x: float = Field(ge=0, lt=100)
    crop_y: float = Field(ge=0, lt=100)
    crop_w: float = Field(gt=0, le=100)
    crop_h: float = Field(gt=0, le=100)
    name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def crop_must_fit_sheet(self):
        if self.crop_x + self.crop_w > 100 or self.crop_y + self.crop_h > 100:
            raise ValueError("Crop must fit within the source sheet")
        return self


class FlashDesignListResponse(BaseModel):
    designs: list[FlashDesignResponse]
    total: int


class FlashSheetListResponse(BaseModel):
    sheets: list[FlashSheetResponse]
    total: int


class FlashSheetInventoryResponse(BaseModel):
    """Whole-sheet storefront result with AI-listed designs and no segmentation."""

    sheet: FlashSheetResponse
    designs: list[FlashDesignResponse]


# ── Public sheet wall schema ─────────────────────────


class PublicSheetResponse(BaseModel):
    id: str
    image_url: str
    title: str | None = None
    design_count: int
    designs: list[FlashDesignResponse]


class PublicSheetWallResponse(BaseModel):
    sheets: list[PublicSheetResponse]
    artist_slug: str
    artist_name: str
    flash_deposit_amount: float
    allow_prepay_full: bool


# ── Flash booking schema ─────────────────────────────


class FlashBookRequest(BaseModel):
    design_id: str = Field(..., description="Flash design UUID to book")
    scheduled_date: str = Field(..., description="YYYY-MM-DD")
    scheduled_time: str = Field(..., description="HH:MM (24h)")
    guest_name: str = Field(..., min_length=2, max_length=255)
    guest_email: str = Field(..., min_length=5, max_length=255)
    guest_phone: str | None = Field(None, max_length=50)
    pay_full: bool = Field(False, description="True = pay full price, False = deposit only")
    hold_token: str | None = Field(
        None,
        max_length=100,
        description="Optional active slot hold (POST /calendar/slot-holds, booking_kind=flash) for this date/time",
    )


class FlashBookResponse(BaseModel):
    booking_reference: str
    checkout_url: str | None
    checkout_required: bool
    amount_cents: int
    is_deposit: bool
    bttcoin_reward: int
    artist_name: str
    design_name: str
    scheduled_date: str
    scheduled_time: str
    account_setup_token: str | None = None
