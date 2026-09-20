import {Check,ChevronRight,LoaderCircle} from 'lucide-react';
import type {SearchReview} from './searchReview';

export default function SiteReviewActivity({review,index,onFinish}:{review:SearchReview;index:number;onFinish:()=>void}) {
 return <section className="assistant-site-review" aria-label="Site evidence review">
  <header><span>Reviewing site evidence</span><small>{index+1} / {review.stops.length}</small></header>
  <ol className="site-review-actions">{review.stops.slice(0,index+1).map((stop,i)=>
   <li key={stop.candidate.id} className={i===index?'is-running':'is-complete'}>
    <span className="site-action-icon">{i===index?<LoaderCircle size={13} className="spin"/>:<Check size={13}/>}</span>
    <div><span className="site-action-name">Review site evidence</span><strong>{stop.candidate.name}</strong>
     <p className={stop.excluded?'site-action-constraint':''}>{stop.label}</p>
     <details><summary>Measured checks<ChevronRight size={11}/></summary>{stop.facts.map(f=><p key={f}>{f}</p>)}</details>
    </div>
   </li>
  )}</ol>
  <button onClick={onFinish}>Show shortlist now<ChevronRight size={13}/></button>
 </section>;
}
