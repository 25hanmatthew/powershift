export interface FleetMonth {month:number;capacity_mw:number;hours:number;actual_mwh:number;baseline_mwh:number;weather_mwh:number;wind_only_mwh:number;actual_cf:number;baseline_cf:number;weather_cf:number;wind_delta:number;wind_mps:number;temperature_c:number;coverage:number;station_count:number;reference_years:number;contributions_cf_points:Record<string,number>}
export interface FleetStation {id:string;name:string;latitude:number|null;longitude:number|null;distance_km:number}
export interface FleetPlant {id:number;name:string;state:string;county:string;latitude:number;longitude:number;capacity_mw:number;stations:FleetStation[];months:FleetMonth[]}
export interface FleetData {version:string;test_year:number;task:string;benchmark:{rows:number;plants:number;states:number;training_rows:number;scores:Record<string,{mae_cf_points:number}>;weather_gain:{gain_pct:number;lower_pct:number;upper_pct:number};low_wind:{paired_plants:number;difference_cf_points:number};wind_only_gain_share_pct:number;weather_vs_wind_only:{lower_pct:number;upper_pct:number};shortfalls:{months_below_historical_cf_by_10_points:number;months_still_below_weather_model_by_10_points:number}};quality:{eligible_2024_label_rows:number;joined_rows:number;stations:number;retained_fraction:number};sources:{name:string;url:string}[];provenance:{pudl_version:string;model_sha256:string;evaluation_sha256:string;predictions_sha256:string;audits:string[]};plants:FleetPlant[]}
export const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
export const observation=(plant:FleetPlant,month:number)=>plant.months.find(row=>row.month===month);
export const gapPoints=(row:FleetMonth,weather=true)=>(row.actual_cf-(weather?row.weather_cf:row.baseline_cf))*100;
export const flagged=(row:FleetMonth,weather=true)=>gapPoints(row,weather)<-10;
export function bridge(row:FleetMonth) {return {baseline:row.baseline_mwh,weather:row.weather_mwh-row.baseline_mwh,residual:row.actual_mwh-row.weather_mwh,actual:row.actual_mwh};}
export function distanceKm(a:[number,number],b:[number,number]) {const radians=(x:number)=>x*Math.PI/180;const [lon1,lat1,lon2,lat2]=[...a,...b].map(radians);return 12742*Math.asin(Math.min(1,Math.sqrt(Math.sin((lat2-lat1)/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin((lon2-lon1)/2)**2)));}
export function fleetTotals(plants:FleetPlant[],month:number,weather=true) {
 const rows=plants.map(p=>observation(p,month)).filter((row):row is FleetMonth=>Boolean(row));
 return {observed:rows.length,missing:plants.length-rows.length,flagged:rows.filter(row=>flagged(row,weather)).length,
  actual_mwh:rows.reduce((sum,row)=>sum+row.actual_mwh,0),
  flagged_gap_mwh:rows.filter(row=>flagged(row,weather)).reduce((sum,row)=>sum+(weather?row.weather_mwh:row.baseline_mwh)-row.actual_mwh,0)};
}
