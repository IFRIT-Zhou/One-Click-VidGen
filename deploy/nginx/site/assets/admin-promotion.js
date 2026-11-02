(function(){
  "use strict";
  const key="ocvg-cloud-session", content=document.querySelector("#page-content"), message=document.querySelector("#page-message");
  const esc=value=>String(value??"").replace(/[&<>\'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
  const date=value=>value?new Date(value).toLocaleString("zh-CN"):"—";
  function session(){try{return JSON.parse(sessionStorage.getItem(key));}catch{return null;}}
  async function api(path,options={}){
    const initial=session();if(!initial?.access_token||initial.user?.role!=="admin"){location.href="/admin/";throw new Error("请登录管理员账户");}
    const auth=window.OCVGSessionRefresh.session;let sent=auth.current(initial);
    const send=()=>fetch(`/api/v1${path}`,{cache:"no-store",...options,headers:{Accept:"application/json",...(options.body?{"Content-Type":"application/json"}:{}),Authorization:`Bearer ${sent.access_token}`,...options.headers}});
    let response=await send();if(response.status===401){await auth.refresh(sent);sent=auth.current(initial);response=await send();}
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.message||data.detail||"请求失败");return data?.data&&data.code!==undefined?data.data:data;
  }
  function setMessage(text,success=false){message.textContent=text;message.className=`admin-message${text?` show ${success?"success":"error"}`:""}`;}
  async function load(){try{
    const data=await api("/admin/referral-promotion-claims?status_filter=pending");const rows=data.items||[];
    document.querySelector("#page-summary").textContent=`待审核 ${rows.length} 项`;
    content.innerHTML=rows.length?`<div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>申请人</th><th>平台账号</th><th>公开视频</th><th>提交时间</th><th>审核</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(x.email)}</td><td>${esc(x.platform)} · ${esc(x.account_name)}</td><td><a href="${esc(x.video_url)}" target="_blank" rel="noopener noreferrer">打开视频</a></td><td>${esc(date(x.submitted_at))}</td><td><div class="table-actions"><button class="table-button" data-review="approve" data-id="${esc(x.id)}">通过并发 30 分</button><button class="table-button danger" data-review="reject" data-id="${esc(x.id)}">拒绝</button></div></td></tr>`).join("")}</tbody></table></div>`:'<p class="admin-empty">当前没有待审核申请</p>';
    content.querySelectorAll("[data-review]").forEach(button=>button.addEventListener("click",async()=>{
      const decision=button.dataset.review;let note=null;if(decision==="reject"){note=prompt("填写拒绝原因");if(note===null)return;}
      content.querySelectorAll("button").forEach(item=>item.disabled=true);
      try{await api(`/admin/referral-promotion-claims/${encodeURIComponent(button.dataset.id)}/review`,{method:"POST",body:JSON.stringify({decision,note})});setMessage(decision==="approve"?"审核通过，奖励积分已发放。":"申请已拒绝。",true);await load();}
      catch(error){setMessage(error.message);content.querySelectorAll("button").forEach(item=>item.disabled=false);}
    }));setMessage("");
  }catch(error){setMessage(error.message);content.textContent="无法读取待审核申请。";}}
  document.querySelector("#admin-identity").textContent=session()?.user?.email||"管理员";
  document.querySelector("#admin-logout").addEventListener("click",()=>{sessionStorage.removeItem(key);location.href="/admin/";});
  document.querySelector("#page-reload").addEventListener("click",load);load();
})();
