from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 'wind-residual-v1'
FEATURES = ['resource_expected_capacity_factor', 'isd_mean_wind_mps', 'isd_wind_std_mps',
            'isd_wind_p10_mps', 'isd_wind_p90_mps', 'isd_mean_temp_c',
            'isd_station_count', 'isd_nearest_station_km', 'isd_coverage_fraction',
            'elevation_m', 'slope_deg', 'natural_pct', 'developed_pct', 'month_sin', 'month_cos']
BASELINE_ID = 'vce-rare-onshore-wind-monthly'
PLANT_SCHEMA_VERSION = 'wind-plant-residual-v3'
WIND_FEATURES = ['era5_wind100_mean_mps','era5_wind100_std_mps','era5_power_proxy_cf',
                 'era5_low_wind_fraction','era5_high_wind_fraction','era5_wind10_mean_mps',
                 'era5_shear_ratio','era5_temperature_c','era5_surface_pressure_pa']
PLANT_FEATURES = FEATURES + WIND_FEATURES
PHYSICAL_FEATURES = ['resource_expected_capacity_factor'] + WIND_FEATURES + ['elevation_m','slope_deg','natural_pct','developed_pct','month_sin','month_cos']
SCHEMAS = {SCHEMA_VERSION:[FEATURES], PLANT_SCHEMA_VERSION:[PLANT_FEATURES,PHYSICAL_FEATURES]}
ATLAS_SCHEMA_VERSION = 'wind-atlas-residual-v4'
ATLAS_FEATURES = PHYSICAL_FEATURES + ['gwa_wind100_mps','gwa_wind100_mean_2km_mps','gwa_wind100_std_2km_mps','gwa_wind_ratio','gwa_scaled_power_proxy_cf']
SCHEMAS[ATLAS_SCHEMA_VERSION] = [PHYSICAL_FEATURES,ATLAS_FEATURES]


class FactorContribution(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    feature: str
    contribution_cf: float


class MLFields(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    ml_enabled: bool = False
    ml_model_version: str | None = None
    ml_base_expected_cf: float | None = Field(None, ge=0, le=1)
    ml_predicted_residual_cf: float | None = None
    ml_corrected_expected_cf: float | None = Field(None, ge=0, le=1)
    ml_interval_low_cf: float | None = Field(None, ge=0, le=1)
    ml_interval_high_cf: float | None = Field(None, ge=0, le=1)
    ml_confidence: Literal['HIGH','MEDIUM','LOW'] | None = None
    ml_top_factors: list[FactorContribution] | None = None
    ml_training_similarity_score: float | None = Field(None, ge=0, le=1)
    ml_fallback_reason: str | None = None
    ml_adjustment_cf: float = 0.0
    ml_explanation_bias_cf: float | None = None


class QualityGateError(ValueError):
    """A measured quality gate failed. Later pipeline phases must not run."""
