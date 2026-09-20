import { ArrowLeft,Sun,Wind } from 'lucide-react';
import type { Candidate } from './types';
import type { EquipmentSelection } from './equipmentSelection';
import { EQUIPMENT } from './siteConcept';
import type { SiteLayout } from './siteConcept';

export default function EquipmentDetail({equipment:e,layout,candidate,onClose}:{equipment:EquipmentSelection;layout:SiteLayout;candidate:Candidate;onClose:()=>void}){
 const solar=e.kind==='solar',unitMW=solar?EQUIPMENT.moduleW/1e6/EQUIPMENT.dcAc:EQUIPMENT.turbineMW;
 const annualKWh=candidate.capacity_mw>0?candidate.annual_gwh*1e6*unitMW/candidate.capacity_mw:null;
 const [x,,z]=e.position;
 const lng=layout.center[0]+x/(111320*Math.cos(layout.center[1]*Math.PI/180)),lat=layout.center[1]-z/111320;
 const n=(v:number)=>v.toLocaleString('en-US',{maximumFractionDigits:1});
 const stats=solar?[
  ['Rated power',`${EQUIPMENT.moduleW} W DC`],['AC capacity allocation',`${n(unitMW*1000)} kW`],
  ['Module size','1.3 × 2.4 m'],['Tilt',`${layout.tilt??EQUIPMENT.tilt}°`],
  ['Mounting',layout.surfaceType==='rooftop'?'Rooftop':layout.surfaceType?'Shared canopy':'Ground-mounted table'],
  ...(e.tableIndex===undefined?[]:[['Panel table',`#${e.tableIndex+1} · 40 modules`]])
 ]:[['Rated power',`${EQUIPMENT.turbineMW} MW`],['Hub height',`${EQUIPMENT.hubHeight} m`],['Rotor diameter',`${EQUIPMENT.rotorDiameter} m`],['Blade count','3'],['Turbine spacing',`${n(layout.spacingX)} × ${n(layout.spacingZ)} m`]];
 return <section className="equipment-detail" aria-label="Selected equipment statistics" aria-live="polite">
  <button className="equipment-back" onClick={onClose}><ArrowLeft size={15}/>Back to project</button>
  <div className="equipment-heading">{solar?<Sun size={24}/>:<Wind size={24}/>}<div><span>SELECTED EQUIPMENT</span><h3>{solar?'Solar panel':'Wind turbine'} #{e.index+1}</h3></div></div>
  <dl className="equipment-stats">{stats.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
  <div className="equipment-energy"><span>Estimated annual energy</span><strong>{annualKWh!==null&&Number.isFinite(annualKWh)?`${n(annualKWh/(solar?1:1000))} ${solar?'kWh':'MWh'}`:'Unavailable'}</strong><p>Allocated from the site's screening estimate in proportion to capacity. Individual shading, wake losses and equipment performance are not modeled.</p></div>
  <p className="equipment-location">{lat.toFixed(6)}°, {lng.toFixed(6)}°</p>
  <p className="equipment-note">Concept equipment with generic specifications, not installed equipment or live telemetry. Click another panel or turbine to inspect it.</p>
 </section>;
}
