const datasets = {
  human: [
    {model:"HappyHorse-1.0",type:"Proprietary",alignment:.742,consistency:.757,text:.434,audio:.674,subject:.802,action:.753,realism:.698,visual:.808},
    {model:"Veo-3.1-Fast",type:"Proprietary",alignment:.766,consistency:.754,text:.430,audio:.703,subject:.808,action:.730,realism:.628,visual:.781},
    {model:"Kling-3.0",type:"Proprietary",alignment:.701,consistency:.703,text:.264,audio:.589,subject:.768,action:.709,realism:.675,visual:.769},
    {model:"Wan-2.2-14B",type:"Open source",alignment:.485,consistency:.499,text:.170,audio:null,subject:.579,action:.489,realism:.560,visual:.644},
    {model:"HunyuanVideo-1.5",type:"Open source",alignment:.475,consistency:.509,text:.102,audio:null,subject:.584,action:.511,realism:.550,visual:.607},
    {model:"Cosmos-Predict-2.5-14B",type:"Open source",alignment:.403,consistency:.464,text:.178,audio:null,subject:.545,action:.466,realism:.409,visual:.491},
    {model:"LTX-2.3-22B",type:"Open source",alignment:.359,consistency:.385,text:.177,audio:.408,subject:.489,action:.408,realism:.552,visual:.638}
  ],
  gemini: [
    {model:"HappyHorse-1.0",type:"Proprietary",alignment:.907,consistency:.943,text:.439,audio:.896,subject:.992,action:.883,realism:.792,visual:.948},
    {model:"Veo-3.1-Fast",type:"Proprietary",alignment:.915,consistency:.931,text:.456,audio:.783,subject:.994,action:.874,realism:.745,visual:.926},
    {model:"Kling-3.0",type:"Proprietary",alignment:.883,consistency:.909,text:.402,audio:.809,subject:.992,action:.885,realism:.776,visual:.935},
    {model:"Wan-2.2-14B",type:"Open source",alignment:.603,consistency:.727,text:.297,audio:null,subject:.909,action:.650,realism:.671,visual:.861},
    {model:"HunyuanVideo-1.5",type:"Open source",alignment:.624,consistency:.729,text:.263,audio:null,subject:.897,action:.663,realism:.558,visual:.698},
    {model:"Cosmos-Predict-2.5-14B",type:"Open source",alignment:.518,consistency:.653,text:.207,audio:null,subject:.872,action:.523,realism:.410,visual:.507},
    {model:"LTX-2.3-22B",type:"Open source",alignment:.454,consistency:.581,text:.214,audio:.566,subject:.730,action:.502,realism:.513,visual:.697}
  ]
};
let evaluator="gemini", sortKey="average", descending=true;
const numericKeys=["alignment","consistency","text","audio","subject","action","realism","visual"];
const average=row=>numericKeys.reduce((a,k)=>a+(row[k]??0),0)/numericKeys.filter(k=>row[k]!=null).length;
const formatScore=value=>(Math.round((value+1e-12)*1000)/1000).toFixed(3);
function render(){
  const rows=datasets[evaluator].map(r=>({...r,average:average(r)})).sort((a,b)=>{
    if(sortKey==="model") return (descending?-1:1)*a.model.localeCompare(b.model);
    return (descending?-1:1)*((a[sortKey]??-1)-(b[sortKey]??-1));
  });
  const max={};["average",...numericKeys].forEach(k=>max[k]=Math.max(...rows.map(r=>r[k]??-1)));
  document.querySelector("#leaderboard-table tbody").innerHTML=rows.map((r,i)=>`<tr>
    <td class="medal">${i===0?"🥇":i===1?"🥈":i===2?"🥉":i+1}</td>
    <td class="model">${r.model}<small>${r.type}</small></td>
    ${["average",...numericKeys].map(k=>`<td class="${r[k]===max[k]?"best":""}">${r[k]==null?"—":formatScore(r[k])}</td>`).join("")}
  </tr>`).join("");
}
document.querySelectorAll(".toggle button").forEach(button=>button.addEventListener("click",()=>{
  evaluator=button.dataset.evaluator;
  document.querySelectorAll(".toggle button").forEach(b=>b.classList.toggle("active",b===button));
  render();
}));
document.querySelectorAll("th[data-key]").forEach(th=>th.addEventListener("click",()=>{
  if(th.dataset.key==="rank") return;
  descending=sortKey===th.dataset.key?!descending:true; sortKey=th.dataset.key; render();
}));
document.querySelectorAll("video").forEach(video=>video.addEventListener("error",()=>video.classList.add("missing")));
const autoplayVideos=[...document.querySelectorAll(".tldr-videos video")];
function playTldrVideos(){
  autoplayVideos.forEach(video=>{
    video.muted=true;
    video.play().catch(()=>{});
  });
}
autoplayVideos.forEach(video=>{
  if(video.readyState>=3) video.play().catch(()=>{});
  else video.addEventListener("canplay",()=>video.play().catch(()=>{}),{once:true});
});
document.addEventListener("visibilitychange",()=>{if(!document.hidden) playTldrVideos()});
const copyCitation=document.querySelector("#copy-citation");
copyCitation?.addEventListener("click",async()=>{
  const bibtex=document.querySelector("#bibtex")?.textContent??"";
  try{
    await navigator.clipboard.writeText(bibtex);
  }catch{
    const textarea=document.createElement("textarea");
    textarea.value=bibtex;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  copyCitation.textContent="Copied!";
  setTimeout(()=>copyCitation.textContent="Copy BibTeX",1600);
});
render();
