import type { Candidate } from './types';
import type { SiteLayout } from './siteConcept';

export const COST_SOURCE={url:'https://www.eia.gov/electricity/generatorcosts/',title:'EIA · generators installed in 2024',released:'2026-07-06',currency:'constant 2024 USD'};
export interface FinanceInputs {capexPerKW:number;omPerKW:number;pricePerMWh:number;discountPct:number;years:number;degradationPct:number;contingencyPct:number;extraMillions:number;curtailmentPct:number;carbonTonnesPerMWh:number}
export function defaultFinance(technology:'solar'|'wind',surface?:Candidate['surface_type']):FinanceInputs {
 return {capexPerKW:surface?(surface==='rooftop'?3000:4000):technology==='solar'?1865:1882,omPerKW:surface?35:technology==='solar'?25:50,pricePerMWh:55,discountPct:7,years:30,
         degradationPct:technology==='solar'?.5:.2,contingencyPct:10,extraMillions:0,curtailmentPct:0,carbonTonnesPerMWh:.35};
}
export function calculateFinance(candidate:Pick<Candidate,'capacity_mw'|'annual_gwh'>,layout:Pick<SiteLayout,'capacityMW'>,input:FinanceInputs){
 if(Object.values(input).some(v=>!Number.isFinite(v)||v<0)||input.years<1||input.years>60||!Number.isInteger(input.years)||input.degradationPct>100||input.curtailmentPct>100||layout.capacityMW<0||!Number.isFinite(layout.capacityMW)||!Number.isFinite(candidate.capacity_mw)||candidate.capacity_mw<=0||!Number.isFinite(candidate.annual_gwh)||candidate.annual_gwh<0)throw new Error('Invalid financial scenario inputs.');
 const capacityKW=layout.capacityMW*1000;
 const construction=capacityKW*input.capexPerKW,contingency=construction*input.contingencyPct/100;
 const investment=construction+contingency+input.extraMillions*1e6;
 const om=capacityKW*input.omPerKW;
 const firstMWh=candidate.annual_gwh*1000*(layout.capacityMW/candidate.capacity_mw)*(1-input.curtailmentPct/100);
 const decommission=construction*.05;
 let cumulative=-investment,npv=-investment,pvCost=investment,pvEnergy=0,lifetimeMWh=0,payback:number|null=investment===0&&capacityKW>0?0:null;
 const cashflows=[{year:0,energyMWh:0,revenue:0,om:0,net:-investment,cumulative,discounted:-investment}];
 for(let year=1;year<=input.years;year++){
  const energyMWh=firstMWh*(1-input.degradationPct/100)**(year-1),revenue=energyMWh*input.pricePerMWh;
  const endCost=year===input.years?decommission:0;
  const net=revenue-om-endCost,discount=(1+input.discountPct/100)**year,previous=cumulative;
  cumulative+=net;npv+=net/discount;pvCost+=(om+endCost)/discount;pvEnergy+=energyMWh/discount;lifetimeMWh+=energyMWh;
  if(payback===null&&investment>0&&previous<0&&cumulative>=0&&net>0)payback=year-1+(-previous/net);
  cashflows.push({year,energyMWh,revenue,om,net,cumulative,discounted:net/discount});
 }
 const annualRevenue=firstMWh*input.pricePerMWh;
 return {construction,contingency,investment,om,annualRevenue,firstYearCash:annualRevenue-om,firstMWh,lifetimeMWh,npv,payback,
         lcoe:pvEnergy>0?pvCost/pvEnergy:null,avoidedTonnes:firstMWh*input.carbonTonnesPerMWh,cashflows,decommission};
}
