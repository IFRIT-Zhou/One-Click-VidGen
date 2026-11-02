(function(){
  "use strict";
  const auth=window.OCVGSessionRefresh.session, notice=document.querySelector("#invite-notice");
  const message=(text,ok=false)=>{notice.textContent=text;notice.className=`invite-notice${ok?" success":""}`;};
  async function request(path,options={}){
    const initial=auth.read();if(!initial?.access_token)throw new Error("请先登录账户");
    let sent=auth.current(initial);let response=await fetch(`/api/v1${path}`,{cache:"no-store",...options,headers:{Accept:"application/json",...(options.body?{"Content-Type":"application/json"}:{}),Authorization:`Bearer ${sent.access_token}`,...options.headers}});
    if(response.status===401){await auth.refresh(sent);sent=auth.current(initial);response=await fetch(`/api/v1${path}`,{cache:"no-store",...options,headers:{Accept:"application/json",...(options.body?{"Content-Type":"application/json"}:{}),Authorization:`Bearer ${sent.access_token}`,...options.headers}});}
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.message||data.detail||"请求失败");return data?.data&&data.code!==undefined?data.data:data;
  }
  async function load(){try{
    const data=await request("/referrals/me");
    const link=new URL(`/register/?invite=${encodeURIComponent(data.code)}`,location.origin).toString();
    document.querySelector("#invite-code").textContent=data.code;document.querySelector("#invite-link").value=link;
    document.querySelector("#invite-qr").src=`https://quickchart.io/qr?size=180&text=${encodeURIComponent(link)}`;
    document.querySelector("#verified-count").textContent=data.verified_referrals;
    document.querySelector("#pending-count").textContent=data.referrals.length-data.verified_referrals;
    document.querySelector("#bonus-count").textContent=Number(data.bonus_credits.available).toLocaleString("zh-CN");
    document.querySelector("#bonus-expiry").textContent=(data.bonus_credits.expiring||[]).map(x=>`${Number(x.amount).toLocaleString("zh-CN")} 分 · ${new Date(x.expires_at).toLocaleDateString("zh-CN")} 到期`).join("；")||"暂无待到期奖励积分";
    const list=document.querySelector("#claim-list");list.replaceChildren(Object.assign(document.createElement("h3"),{textContent:"我的申请"}));
    for(const item of data.promotion_claims){const row=document.createElement("div");row.className="claim-row";const linkEl=document.createElement("a");linkEl.href=item.video_url;linkEl.target="_blank";linkEl.rel="noopener noreferrer";linkEl.textContent=item.video_url;const state=document.createElement("span");state.className=`claim-status ${item.status}`;state.textContent={pending:"审核中",approved:"已通过 · 已发放 30 积分",rejected:"未通过"}[item.status]||item.status;row.append(`${item.platform} · ${item.account_name} · `,state,document.createElement("br"),linkEl);if(item.review_note)row.append(document.createTextNode(` · ${item.review_note}`));list.append(row);}
    if(!data.promotion_claims.length)list.append(Object.assign(document.createElement("p"),{textContent:"还没有提交申请"}));message("邀请信息已更新",true);
  }catch(error){message(error.message);}}
  document.querySelector("#copy-code").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(document.querySelector("#invite-code").textContent);message("邀请码已复制",true);}catch{message("复制失败，请手动选择邀请码");}});
  document.querySelector("#copy-link").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(document.querySelector("#invite-link").value);message("邀请链接已复制",true);}catch{message("复制失败，请手动复制邀请链接");}});
  document.querySelector("#claim-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget;const button=form.querySelector("button[type=submit]");button.disabled=true;try{const payload=Object.fromEntries(new FormData(form));await request("/referrals/promotion-claims",{method:"POST",body:JSON.stringify(payload)});form.reset();message("申请已提交，审核结果会显示在下方。",true);await load();}catch(error){message(error.message);}finally{button.disabled=false;}});
  load();document.addEventListener("ocvg:account-changed",load);
})();
