"""Global Wind Atlas site climatology, sampled with HTTP range reads."""
import hashlib
import json
import math

import numpy as np
import pandas as pd

from .schema import QualityGateError

URL='https://gwa.cdn.nazkamapps.com/country_tifs_v4/USA_wind-speed_100m.tif'


def site_atlas(directory,frame):
    path=directory/'atlas.parquet'
    plants=frame.drop_duplicates('plant_id_eia')
    if path.exists():
        atlas=pd.read_parquet(path)
    else:
        import rasterio
        from rasterio.windows import from_bounds
        rows=[]
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_USE_HEAD='NO',GDAL_HTTP_TIMEOUT='60'):
            with rasterio.open('/vsicurl/'+URL) as source:
                for index,p in enumerate(plants.itertuples()):
                    lat,lon=float(p.latitude),float(p.longitude)
                    dx=1000/(111320*math.cos(math.radians(lat)));dy=1000/111320
                    window=from_bounds(lon-dx,lat-dy,lon+dx,lat+dy,source.transform)
                    values=source.read(1,window=window,masked=True).compressed()
                    value=float(next(source.sample([(lon,lat)]))[0])
                    if not len(values) or not np.isfinite(values).all() or not 0<value<40 or np.any((values<=0)|(values>=40)):
                        raise QualityGateError(f'Invalid Global Wind Atlas coverage for plant {p.plant_id_eia}.')
                    rows.append({'plant_id_eia':p.plant_id_eia,'gwa_wind100_mps':value,
                                 'gwa_wind100_mean_2km_mps':float(values.mean()),'gwa_wind100_std_2km_mps':float(values.std())})
                    if (index+1)%50==0: print(f'Wind Atlas sites: {index+1}/{len(plants)}',flush=True)
        atlas=pd.DataFrame(rows);atlas.to_parquet(path,index=False)
    if set(atlas.plant_id_eia)!=set(plants.plant_id_eia): raise QualityGateError('Atlas cache plant membership differs.')
    wind=pd.read_parquet(directory/'site-wind.parquet')
    climatology=wind[wind.report_month.dt.year.between(2019,2022)].copy()
    climatology['weighted_wind']=climatology.era5_wind100_mean_mps*climatology.era5_hours
    totals=climatology.groupby('plant_id_eia')[['weighted_wind','era5_hours']].sum()
    average=(totals.weighted_wind/totals.era5_hours).rename('era5_training_climatology')
    atlas=atlas.join(average,on='plant_id_eia')
    atlas['gwa_wind_ratio']=atlas.gwa_wind100_mean_2km_mps/atlas.era5_training_climatology
    valid=np.isfinite(atlas.gwa_wind_ratio)&atlas.gwa_wind_ratio.between(.25,4)
    excluded=atlas[~valid][['plant_id_eia','gwa_wind_ratio']].to_dict('records')
    (directory/'atlas-quality.json').write_text(json.dumps({'plants':len(atlas),'retained_plants':int(valid.sum()),
        'rule':'Atlas / ERA5 climatology ratio must be in [0.25, 4]; retain at least 95% of plants and rows.',
        'excluded_plants':excluded},indent=2),encoding='utf-8')
    result=frame.merge(atlas[valid],on='plant_id_eia',validate='many_to_one')
    if valid.mean()<.95 or len(result)/len(frame)<.95:
        raise QualityGateError('Atlas to weather wind ratios failed 95% coverage requirement.')
    result['gwa_scaled_power_proxy_cf']=np.clip(result.era5_power_proxy_cf*result.gwa_wind_ratio**3,0,1)
    (directory/'atlas-source.json').write_text(json.dumps({
        'title':'Global Wind Atlas 4.0, USA wind speed at 100 m','url':URL,
        'attribution':'Global Wind Atlas 4.0, Technical University of Denmark and World Bank Group, funded by ESMAP',
        'license':'CC BY 4.0','license_url':'https://globalwindatlas.info/about/TermsOfUse',
        'resolution_degrees':.0025,'climatology_years':[2008,2017],
        'sample_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'ratio_reference_years':[2019,2022],
        'power_proxy_note':'Climatology-adjusted cubic covariate; not a turbine-specific engineering yield.'},indent=2),encoding='utf-8')
    return result
