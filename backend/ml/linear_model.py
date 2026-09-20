"""Portable linear residual inference; JSON coefficients, no executable pickles."""
import numpy as np


class LinearModel:
    def __init__(self,payload):
        self.features=payload['feature_order']
        self.coefficients=np.asarray(payload['coefficients'],dtype=float)
        self.intercept=float(payload['intercept'])
        self.background=np.asarray(payload['background_mean'],dtype=float)
        if self.coefficients.shape!=(len(self.features),) or self.background.shape!=self.coefficients.shape:
            raise ValueError('Linear model coefficient dimensions do not match the feature schema.')
        if not np.isfinite(self.coefficients).all() or not np.isfinite(self.background).all() or not np.isfinite(self.intercept):
            raise ValueError('Linear model contains nonfinite parameters.')

    def feature_name(self): return self.features

    def predict(self,rows):
        return np.asarray(rows,dtype=float)@self.coefficients+self.intercept


class LinearExplanation:
    """Exact interventional linear SHAP, centered on training feature means."""
    def __init__(self,model):
        self.model=model
        self.expected_value=float(model.background@model.coefficients+model.intercept)

    def shap_values(self,rows):
        return (np.asarray(rows,dtype=float)-self.model.background)*self.model.coefficients


class OffsetModel:
    def __init__(self,point,offset):
        self.point=point;self.offset=float(offset)

    def predict(self,rows): return self.point.predict(rows)+self.offset


def payload_from_pipeline(pipeline,frame,features):
    scaler=pipeline.steps[0][1];regression=pipeline.steps[-1][1]
    coefficients=regression.coef_/scaler.scale_
    return {'feature_order':features,'coefficients':coefficients.tolist(),
            'intercept':float(regression.intercept_-scaler.mean_@coefficients),
            'background_mean':frame[features].mean().to_list()}
