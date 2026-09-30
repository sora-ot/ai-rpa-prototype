"""Observe live DOM and expose bounded, grounded candidate IDs to the agent."""
import base64
import re
from urllib.parse import urlparse


OBSERVE_JS = r"""
const visible = e => { const r=e.getBoundingClientRect(),s=getComputedStyle(e); return r.width>0&&r.height>0&&s.display!='none'&&s.visibility!='hidden'; };
const absolute = e => { let p=[]; for(let n=e;n&&n.nodeType===1;n=n.parentElement){let i=1;for(let q=n.previousElementSibling;q;q=q.previousElementSibling)if(q.tagName===n.tagName)i++;p.unshift(n.tagName.toLowerCase()+'['+i+']');}return '/'+p.join('/');};
const unique = path => {try{return document.evaluate('count('+path+')',document,null,XPathResult.NUMBER_TYPE,null).numberValue===1;}catch{return false;}};
const literal = value => value.includes('"') ? (value.includes("'") ? null : "'"+value+"'") : '"'+value+'"';
const nextToken = e => {
 const label=(e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\s+/g,' ');
 if(/上一页|上页|\b(prev|previous)\b/i.test(label))return '';
 if(label.includes('下一页')||label.includes('下页'))return '下一页';
 if(/\bnext\b/i.test(label))return 'next';
 if(e.getAttribute('rel')==='next'||(e.parentElement&&e.parentElement.matches('li.next')))return 'rel';
 return '';
};
const isNext = e => e.matches('a,button') && !!nextToken(e);
const xp = e => {
 if(e.tagName.toLowerCase()==='a' && e.parentElement && e.parentElement.matches('li.next')){
  const path='//li[contains(concat(" ",normalize-space(@class)," ")," next ")]/a';
  if(unique(path))return path;
 }
 const token=isNext(e)?nextToken(e):'',tag=e.tagName.toLowerCase();
 if(token==='下一页'){
  const path='//'+tag+'[contains(normalize-space(.),"下一页") or contains(normalize-space(.),"下页")]';
  if(unique(path))return path;
 }
 if(token==='next'){
  const path='//'+tag+'[contains(translate(normalize-space(.),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"next")]';
  if(unique(path))return path;
 }
 if(e.getAttribute('rel')==='next'){
  const path='//'+tag+'[@rel="next"]';if(unique(path))return path;
 }
 const role=e.getAttribute('role'),label=(e.innerText||'').trim().replace(/\s+/g,' ');
 if((role==='tab'||role==='button') && label.length>0 && label.length<=30){
  const quoted=literal(label),quotedRole=literal(role);
  if(quoted && quotedRole){
   const path='//'+tag+'[@role='+quotedRole+' and normalize-space(.)='+quoted+']';
   if(unique(path))return path;
  }
 }
 for(const attr of ['id','data-testid','name','aria-label']){
  const value=e.getAttribute(attr),quoted=value&&literal(value);
  if(quoted){const path='//'+e.tagName.toLowerCase()+'[@'+attr+'='+quoted+']';if(unique(path))return path;}
 }
 const cls=e.classList&&e.classList[0];if(cls && /^[\w-]+$/.test(cls)){
  const path='//'+e.tagName.toLowerCase()+'[contains(concat(" ",normalize-space(@class)," ")," '+cls+' ")]';if(unique(path))return path;
 }
 return absolute(e);
};
let els=[], lists=[], seen=new Set();
const editable = e => {
 const tag=e.tagName.toLowerCase(),type=(e.getAttribute('type')||'').toLowerCase();
 if(tag==='input')return !['hidden','button','submit','reset','checkbox','radio','file','password'].includes(type)&&!e.disabled&&!e.readOnly;
 if(tag==='textarea')return !e.disabled&&!e.readOnly;
 return e.isContentEditable || e.getAttribute('role')==='textbox';
};
const safeUrl = value => {
 try { const u=new URL(value,location.href); for(const key of [...u.searchParams.keys()])
   if(/token|secret|session|auth|code|key|password|credential/i.test(key))u.searchParams.set(key,'[redacted]');
   u.hash=''; return u.href; } catch { return ''; }
};
const selector='a,button,input,textarea,select,[contenteditable="true"],[role="textbox"],img,source,h1,h2,h3,h4,p,span,small,strong,code,time,td,th,article,li,section,div';
const focus=[...document.querySelectorAll('main,[role="main"],section')];
const priorityLinks=[...document.querySelectorAll('a,button')].filter(isNext);
// EasySpider's native list loop needs one XPath matching sibling records and
// relative field XPaths inside its first record. Rank repeated sibling groups
// before allocating the bounded element budget to navigation and filters.
const groupsFound=[];
for(const parent of document.querySelectorAll('main,article,section,ul,ol,table,div')){
 const children=[...parent.children].filter(visible);if(children.length<2||children.length>100)continue;
 const groups=new Map();
 for(const e of children){let key=e.tagName+'|'+(e.className&&typeof e.className==='string'?e.className.trim().split(/\s+/).slice(0,2).join(' '):'');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(e);}
 for(const group of groups.values()){
  if(group.length<2||group.length>80)continue;
  const first=group[0],sample=(first.innerText||'').trim().replace(/\s+/g,' ').slice(0,350);
  const links=first.querySelectorAll('a[href]').length,descendants=first.querySelectorAll('*').length;
  const price=/[¥￥£€$]\s*\d|\b(?:price|USD|CNY)\b|价格/i.test(sample);
  const sales=/已售|销量|人付款|人收货|\bsold\b|\breviews?\b/i.test(sample);
  const score=Math.min(group.length,50)+Math.min(descendants,60)/6+
    (sample.length>=30?10:0)+(price?25:0)+(sales?20:0)+(links?8:0);
  groupsFound.push({parent,group,score});
 }
}
groupsFound.sort((a,b)=>b.score-a.score);
for(const {parent,group,score} of groupsFound.slice(0,20)){
 const first=group[0],tag=first.tagName.toLowerCase();let path=xp(parent)+'/'+tag;
 const cls=first.className&&typeof first.className==='string'?first.className.trim().split(/\s+/)[0]:'';
 if(cls && /^[\w-]+$/.test(cls))path+='[contains(concat(" ",normalize-space(@class)," ")," '+cls+' ")]';
 lists.push({id:'l'+(lists.length+1),xpath:path,first_xpath:absolute(first),count:group.length,
  score:Math.round(score),samples:group.slice(0,2).map(x=>(x.innerText||'').trim().replace(/\s+/g,' ').slice(0,180))});
}
const priorityControls=[...document.querySelectorAll('input,textarea,select,button,a,[role="tab"],[role="button"]')]
 .filter(e=>visible(e)&&(editable(e)||/销量|排序|搜索|下一页|\b(sort|search|next)\b/i.test(
   (e.innerText||'')+' '+(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('placeholder')||'')))).slice(0,35);
const recordFields=groupsFound.slice(0,3).flatMap(({group})=>
 group.slice(0,2).flatMap(root=>[root,...root.querySelectorAll(selector)]));
const ordered=[...priorityLinks,...priorityControls,...recordFields,
 ...focus.flatMap(root=>[root,...root.querySelectorAll(selector)]),...document.querySelectorAll(selector)];
for(const e of ordered){
 if(els.length>=160)break; if(!visible(e))continue;
 const fieldType=(e.getAttribute('type')||'').toLowerCase();
 if(e.matches('input') && (fieldType==='password'||fieldType==='hidden'||
   /token|secret|session|auth|otp|verification|password/i.test((e.name||'')+' '+(e.id||''))))continue;
 const editables=editable(e)?[e]:[...e.querySelectorAll('input,textarea,[contenteditable="true"],[role="textbox"]')].filter(x=>visible(x)&&editable(x));
 const tag=e.tagName.toLowerCase(),txt=(e.innerText||'').trim().replace(/\s+/g,' ').slice(0,130);
 if(!txt && !e.getAttribute('title') && !e.getAttribute('placeholder') && !e.getAttribute('href') && !e.getAttribute('src') && !editables.length)continue;
 if((tag==='div'||tag==='section'||tag==='span') && e.children.length>6)continue;
 if((tag==='div'||tag==='section') && txt.length>200)continue;
 const path=xp(e); if(seen.has(path))continue;seen.add(path);
 const r=e.getBoundingClientRect();els.push({id:'e'+(els.length+1),tag,text:txt,class_name:typeof e.className==='string'?e.className.trim().split(/\s+/).slice(0,3).join(' '):'',title_attr:e.getAttribute('title')||'',placeholder:e.getAttribute('placeholder')||'',aria_label:e.getAttribute('aria-label')||'',href:e.getAttribute('href')?safeUrl(e.getAttribute('href')):'',role:e.getAttribute('role')||'',type:e.getAttribute('type')||'',xpath:path,absolute_xpath:absolute(e),editable:editable(e),editable_descendants:editables.length,editable_xpath:editables.length===1?xp(editables[0]):'',is_next:isNext(e),selected:e.getAttribute('aria-selected')==='true'||e.getAttribute('aria-pressed')==='true',bbox:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]});
}
return {url:safeUrl(location.href),title:document.title,text:(document.body.innerText||'').slice(0,4500),elements:els,lists:lists,
 next_candidates:els.filter(e=>e.is_next).map(e=>({id:e.id,text:e.text,href:e.href,xpath:e.xpath}))};
"""


RISK_WORDS = ("支付", "付款", "购买", "下单", "提交订单", "删除", "发送", "转账", "pay", "checkout", "delete", "purchase", "send", "confirm order")


def observe(driver, with_screenshot=False):
    result = driver.execute_script(OBSERVE_JS)
    if with_screenshot:
        result["screenshot"] = base64.b64encode(driver.get_screenshot_as_png()).decode("ascii")
    return result


def candidate(snapshot, candidate_id, kind="elements"):
    found = next((x for x in snapshot[kind] if x["id"] == candidate_id), None)
    if not found:
        labels = {"elements": "元素", "lists": "列表"}
        label = labels.get(kind, kind)
        available = "、".join(str(x.get("id")) for x in snapshot.get(kind, [])
                               if x.get("id"))
        suffix = available or "无"
        raise ValueError(f"{label}候选 ID 不存在：{candidate_id}；当前可用{label} ID：{suffix}")
    return found


def input_target(snapshot, candidate_id):
    selected = candidate(snapshot, candidate_id)
    if selected.get("editable_xpath"):
        return selected["editable_xpath"]
    suggestions = [e["id"] for e in snapshot["elements"] if e.get("editable_xpath")][:8]
    raise ValueError("所选元素不可输入。可选输入目标 ID：" + ("、".join(suggestions) or "无"))


_RISK_ACTION_WORDS = (
    "click", "press", "execute", "perform", "confirm", "submit",
    "\u70b9\u51fb", "\u6309\u4e0b", "\u6267\u884c", "\u8fdb\u884c", "\u53d1\u8d77", "\u64cd\u4f5c", "\u786e\u8ba4",
    "\u7acb\u5373", "\u9a6c\u4e0a",
)
_RISK_NEGATIONS = (
    "\u4e0d", "\u4e0d\u8981", "\u7981\u6b62", "\u907f\u514d", "\u65e0\u9700", "\u4e0d\u6267\u884c", "\u4e0d\u70b9\u51fb",
    "do not", "don't", "never", "avoid",
)
_RISK_DATA_SUFFIXES = set("\u91cf\u6570\u6b21\u4fe1\u606f\u6570\u636e\u6307\u6807\u540d\u79f0\u5b57\u6bb5")


def _has_direct_risk_intent(label):
    """Recognise an actionable risk target without blocking mere mentions."""
    lowered = label.lower()
    for word in RISK_WORDS:
        term = str(word).lower()
        start = 0
        while True:
            index = lowered.find(term, start)
            if index < 0:
                break
            prefix = lowered[max(0, index - 40):index]
            if any(re.search(rf"{re.escape(neg)}[^,;:!?，；：！？]*$", prefix)
                   for neg in _RISK_NEGATIONS):
                start = index + len(term)
                continue
            if any(action in prefix for action in _RISK_ACTION_WORDS):
                return True
            if index == 0 or lowered[index - 1] in " \t(/[|,-_":
                after = lowered[index + len(term):index + len(term) + 2]
                data_field = term in ("purchase", "\u8d2d\u4e70")
                if not data_field or not any(char in _RISK_DATA_SUFFIXES for char in after):
                    return True
            start = index + len(term)
    return False


def check_action_target(element, action):
    if action != "click":
        return
    # Search/filter forms commonly use a button labelled “提交”. It is not
    # itself a destructive action. Risk checks apply to actionable controls,
    # while a list container or product text may merely mention a keyword.
    if element.get("tag") not in ("a", "button", "input") and element.get("role") != "button":
        return
    label = " ".join(str(element.get(k, "")) for k in ("text", "href", "role")).lower()
    if _has_direct_risk_intent(label):
        raise ValueError("检测到支付、删除、发送等高风险按钮，已停止")


def validate_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or p.username or p.password:
        raise ValueError("请输入普通 http(s) 网页 URL")
    return url
