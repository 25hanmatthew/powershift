import pandas as pd
import pytest

from backend.ml.plant_data import clean_labels
from backend.ml.schema import QualityGateError


def sources():
    month=pd.Timestamp('2021-01-01')
    generators=pd.DataFrame([{'plant_id_eia':plant,'generator_id':str(unit),'report_month':month,
        'capacity_mw':10.,'latitude':40.,'longitude':-110.,'state':'WY','county':'Example',
        'technology_description':'Onshore Wind Turbine','generator_operating_date':pd.Timestamp('2010-01-01'),
        'generator_retirement_date':pd.NaT} for plant in range(1,7) for unit in [1,2]])
    generation=pd.DataFrame([{'plant_id_eia':plant,'report_month':month,'net_generation_mwh':20*744*.4} for plant in range(1,7)])
    reporting=pd.DataFrame([{'plant_id_eia':plant,'report_date':month,'reporting_frequency_code':'M'} for plant in range(1,7)])
    return generators,generation,reporting


def test_actual_monthly_values_include_am_but_exclude_estimated_annual_values():
    g,y,r=sources()
    r.loc[r.plant_id_eia==2,'reporting_frequency_code']='A'
    r.loc[r.plant_id_eia==3,'reporting_frequency_code']='AM'
    r.loc[r.plant_id_eia==4,'reporting_frequency_code']=None
    f=clean_labels(g,y,r)
    assert set(f.plant_id_eia)=={1,3,5,6}
    assert f.actual_capacity_factor.to_list()==pytest.approx([.4]*4)
    assert set(f.label_quality)=={'gold'}
    assert set(f.generators_count)=={2}


def test_mixed_technology_and_partial_operating_months_are_excluded():
    g,y,r=sources()
    g.loc[(g.plant_id_eia==1)&(g.generator_id=='2'),'technology_description']='Solar Photovoltaic'
    g.loc[(g.plant_id_eia==2)&(g.generator_id=='2'),'generator_operating_date']=pd.Timestamp('2021-01-15')
    g.loc[(g.plant_id_eia==3)&(g.generator_id=='2'),'generator_retirement_date']=pd.Timestamp('2021-01-15')
    f=clean_labels(g,y,r)
    assert set(f.plant_id_eia)=={4,5,6}


def test_old_retired_unit_is_not_in_capacity_denominator():
    g,y,r=sources()
    g.loc[(g.plant_id_eia==1)&(g.generator_id=='2'),'generator_retirement_date']=pd.Timestamp('2020-01-01')
    f=clean_labels(g,y,r).set_index('plant_id_eia')
    assert f.loc[1,'capacity_mw']==10
    assert f.loc[1,'actual_capacity_factor']==pytest.approx(.8)


def test_invalid_output_and_unknown_capacity_cannot_be_gold_labels():
    g,y,r=sources()
    y.loc[y.plant_id_eia==1,'net_generation_mwh']=-1
    y.loc[y.plant_id_eia==2,'net_generation_mwh']=999999
    y.loc[y.plant_id_eia==3,'net_generation_mwh']=float('nan')
    g.loc[(g.plant_id_eia==4)&(g.generator_id=='2'),'capacity_mw']=float('nan')
    assert set(clean_labels(g,y,r).plant_id_eia)=={5,6}


@pytest.mark.parametrize('duplicate',['generator','generation','reporting'])
def test_duplicate_source_keys_fail(duplicate):
    g,y,r=sources()
    if duplicate=='generator': g=pd.concat([g,g.iloc[:1]],ignore_index=True)
    if duplicate=='generation': y=pd.concat([y,y.iloc[:1]],ignore_index=True)
    if duplicate=='reporting': r=pd.concat([r,r.iloc[:1]],ignore_index=True)
    with pytest.raises(QualityGateError,match='Duplicate'):
        clean_labels(g,y,r)


def test_weather_station_history_matches_requested_years(tmp_path):
    from backend.ml.data import station_catalog
    pd.DataFrame({'USAF':['000001','000002'],'WBAN':['00001','00002'],'CTRY':['US','US'],
                  'LAT':[40,40],'LON':[-110,-110],'BEGIN':['20100101','20200101'],'END':['20231231','20231231']}).to_csv(tmp_path/'isd-history.csv',index=False)
    assert len(station_catalog(tmp_path,[2021,2022,2023]))==2
    assert len(station_catalog(tmp_path,[2019,2020,2021,2022,2023]))==1


def test_compressed_isd_preserves_dates_units_and_qc():
    import gzip
    from backend.ml.data import decode_isd_archive,aggregate_station
    def record(hour,wind,temperature):
        line=list(' '*105)
        line[15:27]=f'20210101{hour:02d}00'
        line[60:70]=wind
        line[87:93]=temperature
        return ''.join(line)
    content='\n'.join([record(0,'0201N01001','+01001'),record(1,'0201N02005','+02005'),record(2,'0201N99999','+99999')])
    raw=decode_isd_archive(gzip.compress(content.encode('ascii')))
    summary=aggregate_station(raw).iloc[0]
    assert raw.DATE.iloc[0]==pd.Timestamp('2021-01-01')
    assert summary.isd_mean_wind_mps==15
    assert summary.isd_mean_temp_c==15
    assert summary.isd_coverage_fraction==pytest.approx(2/744)
