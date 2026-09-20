import json
import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import HuberRegressor

from backend.ml.linear_model import LinearModel,LinearExplanation,OffsetModel,payload_from_pipeline


def test_portable_model_and_explanations_reconcile_after_json_roundtrip():
    rng=np.random.default_rng(22)
    frame=pd.DataFrame(rng.normal(size=(120,3)),columns=['wind','slope','baseline'])
    target=frame.wind*.04-frame.slope*.01+frame.baseline*.03+.05
    fitted=make_pipeline(StandardScaler(),HuberRegressor()).fit(frame,target)
    payload=json.loads(json.dumps(payload_from_pipeline(fitted,frame,list(frame.columns))))
    model=LinearModel(payload);explanation=LinearExplanation(model)
    assert np.allclose(fitted.predict(frame),model.predict(frame),atol=1e-12)
    assert np.allclose(explanation.shap_values(frame).sum(axis=1)+explanation.expected_value,model.predict(frame),atol=1e-12)
    assert np.allclose(OffsetModel(model,-.1).predict(frame),model.predict(frame)-.1)


@pytest.mark.parametrize('change',[{'coefficients':[.1]}, {'coefficients':[float('nan'),.2]}, {'intercept':float('inf')}])
def test_portable_model_rejects_invalid_coefficients(change):
    payload={'feature_order':['a','b'],'coefficients':[.1,.2],'intercept':0,'background_mean':[0,0]}
    with pytest.raises(ValueError): LinearModel({**payload,**change})
