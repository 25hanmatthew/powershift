from typing import Literal
from pydantic import BaseModel, Field, model_validator
from shapely.geometry import shape

class Weights(BaseModel):
    resource: float = Field(30, ge=0, le=100)
    environment: float = Field(30, ge=0, le=100)
    grid: float = Field(20, ge=0, le=100)
    buildability: float = Field(15, ge=0, le=100)
    reuse: float = Field(5, ge=0, le=100)

class Constraints(BaseModel):
    exclude_protected: bool = True
    max_grid_km: float = Field(25, ge=0, le=100)
    max_slope_deg: float = Field(15, ge=0, le=60)
    zero_new_land: bool = False
    min_capacity_mw: float = Field(0, ge=0, le=10000)

class Plan(BaseModel):
    query: str = Field('Find the lowest-impact way to add 250 MW in northern California and Nevada.', max_length=4000)
    region: Literal['california-nevada', 'sacramento', 'washington', 'us'] = 'california-nevada'
    technology: Literal['auto', 'solar', 'wind'] = 'auto'
    target_mw: float = Field(250, gt=0, le=10000)
    weights: Weights = Field(default_factory=Weights)
    constraints: Constraints = Field(default_factory=Constraints)
    polygon: dict | None = None
    radius_km: float | None = Field(None, gt=0, le=500)
    center: tuple[float, float] | None = None
    mode: Literal['demo', 'live'] = 'demo'
    start_date: str = '2024-01-01'
    end_date: str = '2025-01-01'
    use_operating_evidence: bool = True
    historical_intelligence: bool = False

    @model_validator(mode='after')
    def validate_geo(self):
        from datetime import date
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        if not 1 <= (end - start).days <= 366:
            raise ValueError('Use a date range between one day and one year.')
        if self.polygon:
            geom = shape(self.polygon)
            if geom.geom_type != 'Polygon' or not geom.is_valid or geom.is_empty or len(geom.exterior.coords) > 100:
                raise ValueError('Provide a valid polygon with at most 100 points.')
            if any(abs(x)>180 or abs(y)>90 for x,y in geom.exterior.coords):
                raise ValueError('Polygon coordinates must be longitude/latitude.')
        if self.region == 'us' and not self.polygon:
            raise ValueError('Choose a US city or draw a local boundary before screening.')
        if self.center and (abs(self.center[0]) > 180 or abs(self.center[1]) > 90):
            raise ValueError('Center must be longitude/latitude.')
        if self.radius_km and not self.center:
            raise ValueError('Radius needs a center coordinate.')
        return self

class RerankRequest(BaseModel):
    use_operating_evidence: bool = True
    historical_intelligence: bool = False
    weights: Weights
    constraints: Constraints
    target_mw: float = Field(gt=0, le=10000)
    technology: Literal['auto', 'solar', 'wind'] = 'auto'

REGIONS = {
    'us': {'name': 'United States', 'bounds': [-180, 18, 180, 72]},
    'california-nevada': {'name': 'Northern California + Nevada', 'bounds': [-123.0, 37.3, -117.0, 41.5]},
    'sacramento': {'name': 'Sacramento region', 'bounds': [-122.0, 38.0, -120.8, 39.2]},
    'washington': {'name': 'Eastern Washington', 'bounds': [-120.4, 45.7, -117.0, 48.5]},
}
