"""Save the selected ISD/PUDL development model without enabling live inference."""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.ml.linear_model import LinearModel, payload_from_pipeline
from backend.ml.schema import QualityGateError
from scripts.evaluate_wind_fusion import PortableSpline, spline_payload
from scripts.evaluate_normalized_wind import NormalizedAdapter
from scripts.fusion_wind_models import Fusion
from scripts.station_normalized_wind import ROOT
from scripts.train_isd_pudl_wind import write, digest


def export():
    frame = pd.read_parquet(ROOT/'training.parquet')
    selection = json.loads((ROOT/'selection.json').read_text())
    chosen = selection['selected']
    if chosen is None:
        raise QualityGateError('No normalized ISD candidate passed paired development comparisons.')
    base = Fusion({'method':'baseline','features':'atlas'}).fit(frame)
    correction = NormalizedAdapter(chosen['spec']).fit(frame)
    payload = {'format':'numeric-atlas-spline-ensemble-v1','production_eligible':False,
               'baseline':payload_from_pipeline(base.model,frame,base.features),
               'isd_spline':spline_payload(correction),'fraction':chosen['blend'],
               'reference_years':[2016,2017,2018], 'reference_sha256':digest(ROOT/'climatology.parquet')}
    portable_base = LinearModel(payload['baseline'])
    portable_correction = PortableSpline(payload['isd_spline'])
    expected = (1-chosen['blend'])*base.predict(frame)+chosen['blend']*correction.predict(frame)
    actual = (1-chosen['blend'])*portable_base.predict(frame[base.features])+chosen['blend']*portable_correction.predict(frame)
    if not np.allclose(actual,expected,atol=1e-10,rtol=1e-10):
        raise QualityGateError('Exported normalized ensemble differs from training inference.')
    write(ROOT/'research-wind.json',payload)
    write(ROOT/'export-report.json',{'exported_at':datetime.now(timezone.utc).isoformat(),'model_sha256':digest(ROOT/'research-wind.json'),
          'data_sha256':digest(ROOT/'training.parquet'),'selection_sha256':digest(ROOT/'selection.json'),
          'rows':len(frame),'plants':int(frame.plant_id_eia.nunique()),'maximum_prediction_difference':float(np.max(np.abs(actual-expected))),
          'production_eligible':False,'note':'Development-only results. Independent eastern tests are a separate experiment and cannot be retuned into this model.'})
    print('Numeric normalized ISD/PUDL ensemble saved; live scoring remains unchanged.')


if __name__=='__main__':
    export()
