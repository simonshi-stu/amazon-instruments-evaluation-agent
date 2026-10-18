const $ = id => document.getElementById(id);
const text = (tag, value, parent) => { const el=document.createElement(tag);el.textContent=value;parent.append(el);return el; };
const fmt = v => v === null ? '无法判断' : Number(v).toFixed(3);
let reports=[], current=null;
function bar(parent, label, portions) {
  text('p',label,parent);const row=text('div','',parent);row.className='bar';row.setAttribute('role','img');row.setAttribute('aria-label',label);
  for(const [state,value] of portions){const s=text('span','',row);s.className=state;s.style.width=`${value*100}%`;}
}
function render(r){
  if(r.version!=='synthetic-evaluation-demo.v1'||r.source!=='handwritten_synthetic_only'||r.real_llm!==false)throw Error('不是允许的合成报告');
  const ids=new Set(r.evidence.map(e=>e.id));if(r.claims.some(c=>c.evidence.some(id=>!ids.has(id))))throw Error('结论引用不存在的证据');
  current=r;['cards','error-chart','status-chart','metrics','claims','evidence','limits'].forEach(id=>$(id).replaceChildren());
  $('title').textContent=r.title;
  for(const[label,value]of[['共同成功任务',`${r.comparison.common_n} / ${r.schemes[0].success_only.requested}`],['共同任务的用户数',r.comparison.users],['被排除的任务',r.comparison.excluded_n],['真实模型调用','0 · 未实测']]){const c=text('div',label,$('cards'));c.className='card';text('strong',value,c);}
  for(const key of ['a','b']){const m=r.comparison[key];bar($('error-chart'),`${key.toUpperCase()}：MAE ${fmt(m.mae)}，n=${m.eligible}`,[['success',m.mae===null?0:m.mae/4]]);}
  for(const scheme of r.schemes){const m=scheme.success_only;bar($('status-chart'),`${scheme.name}：成功 ${m.counts.success} / ${m.requested}`,Object.entries(m.counts).map(([k,v])=>[k,v/m.requested]));text('p',`成功 ${m.counts.success} · 回退 ${m.counts.degraded} · 失败 ${m.counts.failed} · 拒绝 ${m.counts.refused}`,$('status-chart')).className='legend';
    for(const[key,label]of[['success_only','仅模型成功'],['including_fallback','系统含回退']]){const v=scheme[key],row=text('tr','',$('metrics'));for(const cell of[`${scheme.name} / ${label}`,`${v.eligible}/${v.requested}`,fmt(v.mae),fmt(v.rmse),v.within_one===null?'无法判断':`${(v.within_one*100).toFixed(1)}%`,fmt(v.bias)])text('td',cell,row);}}
  for(const c of r.claims){const box=text('div','',$('claims'));box.className='claim';text('p',c.text,box);for(const id of c.evidence){const a=text('a',`查看 ${id}`,box);a.href=`#evidence-${id}`;}}
  for(const e of r.evidence){const p=text('p',`${e.label}：${fmt(e.value)}；分母 ${e.denominator}`, $('evidence'));p.id=`evidence-${e.id}`;}
  r.limits.forEach(v=>text('li',v,$('limits')));
  $('provenance').textContent=`工具版本：${r.version}\n输入指纹：${r.input_fingerprint}\n报告指纹：${r.report_fingerprint}\n区间与显著性：未计算\n解释：确定性模板，非 LLM`;
  $('result').hidden=false;document.querySelectorAll('#flow li').forEach(el=>el.classList.add('done'));$('status').textContent='合成报告版本、引用和图表已校验／渲染。Python 数学计算已在构建阶段完成。';
}
$('run').addEventListener('click',()=>{try{render(reports.find(r=>r.scenario===$('scenario').value));}catch{current=null;$('result').hidden=true;$('status').textContent='报告校验失败，未展示指标。';}});
$('scenario').addEventListener('change',()=>{current=null;$('result').hidden=true;document.querySelectorAll('#flow li').forEach(el=>el.classList.remove('done'));$('status').textContent='场景已更改，请重新查看，旧结论已隐藏。';});
$('download').addEventListener('click',()=>{if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`synthetic-demo-${current.scenario}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
try{const response=await fetch('reports.json');if(!response.ok)throw Error();reports=await response.json();if(reports.length!==3)throw Error();$('run').disabled=false;$('status').textContent='三个合成场景已就绪。请选择并查看评测流程。';}catch{$('status').textContent='示例报告加载失败，请检查构建或网络；未生成任何假数据。';}
