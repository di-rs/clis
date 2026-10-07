// Execute the exact inline workflow script with an injected offline GitHub API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const workflow = fs.readFileSync(path.join(__dirname, '../../../.github/workflows/cli-bench.yml'), 'utf8');
const block = workflow.split('            // BEGIN COMMENT SCRIPT\n')[1]?.split('            // END COMMENT SCRIPT')[0] || '';
const source = block.split('\n').map(line => line.replace(/^            /, '')).join('\n');
const execute = new (Object.getPrototypeOf(async function() {}).constructor)('github', 'context', 'core', 'process', source);
const head = 'a'.repeat(40), base = 'b'.repeat(40), marker = '<!-- cli-bench:v1 -->';
function observation(platform='linux', profile='full') {
  return {version:1,platform,profile,suites:[{package:'tailr',outcome:'complete',failure:null,omitted_cases:0,issues:[],cases:[{id:'tiny',roles:[{role:'candidate',elapsed_mean:1.25,rss_mean:42,executable_bytes:123}],comparisons:[{baseline:'previous',candidate:'candidate',ratio:0.9,elapsed_change_percent:-10,direction:profile==='smoke'?'smoke-only':'faster'}]}]}]};
}
function fixture() {
  const writes=[], errors=[], requests=[];
  const pr={number:7,state:'open',head:{sha:head,repo:{full_name:'owner/repo'}},base:{repo:{full_name:'owner/repo'}}};
  const run={id:100,run_number:10,run_attempt:2,event:'pull_request',path:'.github/workflows/cli-bench.yml',repository:{full_name:'owner/repo'},head_repository:{full_name:'owner/repo'},pull_requests:[{number:7,head:{sha:head}}],html_url:'https://github.com/owner/repo/actions/runs/100'};
  const context={repo:{owner:'owner',repo:'repo'},runId:100,eventName:'pull_request',payload:{pull_request:structuredClone(pr)},actor:'human'};
  const env={WORKFLOW_REF:'owner/repo/.github/workflows/cli-bench.yml@refs/pull/7/merge',BENCH_PACKAGES:'["tailr"]',SELECTION_RESULT:'success',CANDIDATE_SHA:head,PREVIOUS_SHA:base,GITHUB_RUN_ATTEMPT:'2'};
  for (const platform of ['linux','macos']) for (const profile of ['smoke','full']) {env[`${platform}_${profile}`.toUpperCase()]=JSON.stringify(observation(platform,profile));env[`${platform}_${profile}_RESULT`.toUpperCase()]='success';}
  const state={writes,errors,requests,pr,run,context,env,comments:[],runs:{100:run},onPull:null};
  const github={request:async (route,args)=>{
    requests.push([route,args]);
    if(route==='GET /repos/{owner}/{repo}/actions/runs/{run_id}') return {data:state.runs[args.run_id]};
    if(route==='GET /repos/{owner}/{repo}/pulls/{pull_number}') {if(state.onPull) state.onPull(); return {data:structuredClone(pr)};}
    if(route==='GET /repos/{owner}/{repo}/issues/{issue_number}/comments') return {data:state.comments};
    if(route.startsWith('POST ')||route.startsWith('PATCH ')) {writes.push([route,args]);return {data:{}};}
    throw Error('Unexpected API route '+route);
  }};
  state.execute=()=>execute(github,context,{setFailed:message=>errors.push(message),info:()=>{}},{env});
  return state;
}
function owned(body,id=9) {return {id,body,user:{login:'github-actions[bot]',type:'Bot'}};}
function metadata(run=100,attempt=2) {return marker+`\n<!-- cli-bench-run:${run}:${attempt}:${head} -->\n<!-- cli-bench-state:${Buffer.from(JSON.stringify({version:1,slots:[]})).toString('base64')} -->\nold`;}
function readState(body) {return JSON.parse(Buffer.from(body.match(/<!-- cli-bench-state:([A-Za-z0-9+/=]+) -->/)[1],'base64').toString());}

test('creates one bounded comment from existing observations and API run URL',async()=>{
  const f=fixture();await f.execute();assert.equal(f.errors.length,0);assert.equal(f.writes.length,1);
  const body=f.writes[0][1].body;assert.ok(body.startsWith(marker));assert.match(body,/1\.25/);assert.match(body,/0\.9/);assert.match(body,/https:\/\/github.com\/owner\/repo\/actions\/runs\/100/);assert.ok(Buffer.byteLength(body)<=60000);
});
test('updates only marked GitHub bot comment and preserves humans and other bots',async()=>{
  const f=fixture();f.comments=[{...owned(metadata()),id:2,user:{login:'person',type:'User'}},{...owned(metadata()),id:3,user:{login:'other[bot]',type:'Bot'}},owned(metadata())];await f.execute();assert.equal(f.writes.length,1);assert.equal(f.writes[0][1].comment_id,9);
});
test('stale head including a head race never writes',async()=>{
  for(const race of [false,true]) {const f=fixture();let reads=0;f.onPull=()=>{if(!race||++reads===2)f.pr.head.sha=base;};await f.execute();assert.equal(f.writes.length,0);}
});
test('older run and older attempt cannot replace newer comment',async()=>{
  for(const other of [{id:101,run_number:11,run_attempt:1},{id:100,run_number:10,run_attempt:3}]) {const f=fixture();f.runs[other.id]={...f.run,...other};f.comments=[owned(metadata(other.id,other.run_attempt))];await f.execute();assert.equal(f.writes.length,0);}
});
test('same-head rerun uses API current attempt and bot metadata is verified',async()=>{
  const f=fixture();f.comments=[owned(metadata(88,1))];f.runs[88]={...f.run,id:88,run_number:9,run_attempt:1,repository:{full_name:'attacker/repo'}};await f.execute();assert.equal(f.writes.length,0);assert.equal(f.errors.length,1);
});
test('fork event or API association and Dependabot never write',async()=>{
  for(const kind of ['eventfork','apifork','dependabot','number','workflow','repository']) {const f=fixture();if(kind==='eventfork')f.context.payload.pull_request.head.repo.full_name='fork/repo';if(kind==='apifork')f.pr.head.repo.full_name='fork/repo';if(kind==='dependabot')f.context.actor='dependabot[bot]';if(kind==='number')f.run.pull_requests[0].number=8;if(kind==='workflow')f.run.path='.github/workflows/other.yml';if(kind==='repository')f.run.repository.full_name='other/repo';await f.execute();assert.equal(f.writes.length,0,kind);}
});
test('malformed, oversized, nonfinite, unknown fields and invalid metrics cause zero mutations',async()=>{
  const bad=[()=>'{',()=> 'x'.repeat(24001),()=>'{"version":NaN}',v=>JSON.stringify({...v,extra:1}),v=>{v.suites[0].cases[0].roles[0].elapsed_mean=-1;return JSON.stringify(v);},v=>{v.suites[0].package='evil';return JSON.stringify(v);},v=>{v.suites[0].cases[0].comparisons[0].direction='made-up';return JSON.stringify(v);}];
  for(const mutate of bad){const f=fixture();f.env.LINUX_FULL=mutate(observation());await f.execute();assert.equal(f.writes.length,0);assert.equal(f.errors.length,1);}
});
test('escapes arbitrary Markdown, HTML, mentions and controls as inert text',async()=>{
  const f=fixture(),v=observation();v.suites[0].cases[0].id='[click](https://evil) <img> @all | `x`\nnext';f.env.LINUX_FULL=JSON.stringify(v);await f.execute();assert.equal(f.writes.length,1);const b=f.writes[0][1].body;assert.ok(!b.includes('<img>'));assert.ok(!b.includes('@all'));assert.ok(!b.includes('[click]'));assert.ok(!b.includes('| `x`'));
});
test('empty impact without retained observations reports no relevant changes',async()=>{
  const f=fixture();f.env.BENCH_PACKAGES='[]';for(const key of Object.keys(f.env))if(key.startsWith('LINUX_')||key.startsWith('MACOS_'))f.env[key]='';f.comments=[owned(metadata())];await f.execute();assert.equal(f.writes.length,1);assert.match(f.writes[0][1].body,/No relevant CLI changes/);assert.ok(!f.writes[0][1].body.includes('1.25'));
});
test('missing OS, failed jobs, selected suite failures and omissions are explicit',async()=>{
  const f=fixture();f.env.MACOS_FULL='';f.env.MACOS_FULL_RESULT='failure';const v=observation();v.suites[0].failure='measurement: incorrect output';v.suites[0].outcome='failed';v.suites[0].cases=[];v.suites[0].omitted_cases=17;f.env.LINUX_FULL=JSON.stringify(v);await f.execute();assert.equal(f.writes.length,1);const b=f.writes[0][1].body;assert.match(b,/macos.*full/);assert.match(b,/unavailable/);assert.match(b,/failure/);assert.match(b,/17 cases omitted/);assert.match(b,/incorrect output/);
});
test('selection failure refreshes the comment with an explicit unavailable notice',async()=>{
 const f=fixture();f.env.SELECTION_RESULT='failure';f.env.BENCH_PACKAGES='';await f.execute();assert.equal(f.writes.length,1);assert.match(f.writes[0][1].body,/Selection unavailable/);
});
test('duplicate owned markers and unbounded comment pagination fail closed',async()=>{
  for(const comments of [[owned(metadata()),owned(metadata(),10)],Array.from({length:50},(_,id)=>({id,body:'human',user:{type:'User',login:'human'}}))]) {const f=fixture();f.comments=comments;await f.execute();assert.equal(f.writes.length,0);assert.equal(f.errors.length,1);assert.ok(f.requests.length<=13);}
});
test('API failures are bounded and diagnostics never include token or response data',async()=>{
 const f=fixture();f.runs={};await f.execute();assert.equal(f.writes.length,0);assert.equal(f.errors.length,1);assert.ok(f.errors[0].length<200);
});
test('maximum compact cases stay under comment byte cap with explicit omissions',async()=>{
  const f=fixture();f.env.BENCH_PACKAGES='["biggie","tailr","mkdirr"]';
  for(const platform of ['linux','macos'])for(const profile of ['smoke','full']){
    const v=observation(platform,profile),suite=v.suites[0];
    suite.cases=Array.from({length:12},(_,i)=>({...structuredClone(suite.cases[0]),id:String(i)+'&'.repeat(125)}));
    suite.omitted_cases=116;
    v.suites=['biggie','tailr','mkdirr'].map(package=>({...structuredClone(suite),package}));
    const raw=JSON.stringify(v);assert.ok(Buffer.byteLength(raw)<=24000);f.env[`${platform}_${profile}`.toUpperCase()]=raw;
  }
  await f.execute();assert.equal(f.errors.length,0);assert.equal(f.writes.length,1);const body=f.writes[0][1].body;assert.ok(Buffer.byteLength(body)<=60000);assert.ok(readState(body).slots.every(slot=>slot.suite.omitted_cases>=116));assert.match(body,/Omitted cases across OS\/profile reports: 1[4-9][0-9][0-9]/);assert.ok(Buffer.byteLength(JSON.stringify(readState(body)))<=22528);
});
test('newer run on a different head replaces old head results and failed run remains visible',async()=>{
 const f=fixture();f.comments=[owned(metadata(99,1).replace(head,base))];f.runs[99]={...f.run,id:99,run_number:9,run_attempt:1,pull_requests:[{number:7,head:{sha:base}}]};f.env.LINUX_FULL_RESULT='failure';await f.execute();assert.equal(f.writes.length,1);assert.ok(f.writes[0][1].body.includes(head));assert.match(f.writes[0][1].body,/full \(failure\)/);
});

function followup(previous, body, packages='[]') {
  const f=fixture();f.context.runId=101;f.run.id=101;f.run.run_number=11;f.run.run_attempt=1;
  f.run.html_url='https://github.com/owner/repo/actions/runs/101';f.run.pull_requests=[{number:7,head:{sha:base}}];
  f.context.payload.pull_request.head.sha=base;f.pr.head.sha=base;
  f.env.GITHUB_RUN_ATTEMPT='1';f.env.CANDIDATE_SHA=base;f.env.BENCH_PACKAGES=packages;
  f.runs={100:structuredClone(previous.run),101:f.run};f.comments=[owned(body)];return f;
}
test('docs-only new head retains prior measurements with original source identity',async()=>{
  const first=fixture();await first.execute();const next=followup(first,first.writes[0][1].body);await next.execute();
  assert.equal(next.errors.length,0);assert.equal(next.writes.length,1);const body=next.writes[0][1].body,state=readState(body);
  assert.equal(state.slots.length,4);assert.ok(state.slots.every(slot=>slot.sha===head&&slot.run_id===100));
  assert.match(body,/Retained results/);assert.match(body,/No relevant CLI changes/);assert.ok(body.includes(head));assert.ok(body.includes('1.25'));
});
test('rerunning another CLI retains skipped slots but failed selected results replace successes',async()=>{
  const first=fixture();await first.execute();const second=followup(first,first.writes[0][1].body,'["mkdirr"]');
  for(const key of ['LINUX_SMOKE','MACOS_SMOKE','LINUX_FULL','MACOS_FULL'])second.env[key]=second.env[key].replaceAll('tailr','mkdirr');
  await second.execute();assert.equal(second.errors.length,0);const merged=readState(second.writes[0][1].body);
  assert.equal(merged.slots.length,8);assert.equal(merged.slots.filter(slot=>slot.suite.package==='tailr').length,4);
  const third=fixture();third.context.runId=102;third.run={...third.run,id:102,run_number:12,run_attempt:1,html_url:'https://github.com/owner/repo/actions/runs/102'};third.env.GITHUB_RUN_ATTEMPT='1';third.runs={100:first.run,101:second.run,102:third.run};
  third.comments=[owned(second.writes[0][1].body)];
  for(const key of ['LINUX_SMOKE','MACOS_SMOKE','LINUX_FULL','MACOS_FULL']){third.env[key]='';third.env[key+'_RESULT']='failure';}
  await third.execute();assert.equal(third.errors.length,0);const replaced=readState(third.writes[0][1].body);
  assert.ok(replaced.slots.filter(slot=>slot.suite.package==='tailr').every(slot=>slot.suite.outcome==='unavailable'&&slot.run_id===102));
  assert.equal(replaced.slots.filter(slot=>slot.suite.package==='mkdirr').length,4);
});
test('corrupt or oversized retained metadata cannot erase existing results',async()=>{
 for(const metadataValue of ['not-base64!',Buffer.from('{bad').toString('base64'),Buffer.from('x'.repeat(22529)).toString('base64')]){
  const f=fixture();f.comments=[owned(metadata().replace(/cli-bench-state:[A-Za-z0-9+/=]+/, 'cli-bench-state:'+metadataValue))];await f.execute();assert.equal(f.writes.length,0);assert.equal(f.errors.length,1);
 }
});
test('retained source is API verified and never turns a foreign run into history',async()=>{
 const first=fixture();await first.execute();const next=followup(first,first.writes[0][1].body);next.runs[100].repository.full_name='attacker/repo';await next.execute();assert.equal(next.writes.length,0);assert.equal(next.errors.length,1);
});
test('all twelve retained source runs fit request budget and are independently verified',async()=>{
 const first=fixture();first.env.BENCH_PACKAGES='["biggie","tailr","mkdirr"]';
 for(const platform of ['linux','macos'])for(const profile of ['smoke','full']){
  const v=observation(platform,profile);v.suites=['biggie','tailr','mkdirr'].map(package=>({...structuredClone(v.suites[0]),package}));first.env[`${platform}_${profile}`.toUpperCase()]=JSON.stringify(v);
 }
 await first.execute();const body=first.writes[0][1].body,state=readState(body),next=followup(first,body);
 state.slots.forEach((slot,i)=>{slot.run_id=i+1;slot.attempt=1;next.runs[i+1]={...first.run,id:i+1,run_number:i+1,run_attempt:1,html_url:`https://github.com/owner/repo/actions/runs/${i+1}`};});
 next.run.run_number=20;next.comments=[owned(body.replace(/cli-bench-state:[A-Za-z0-9+/=]+/,'cli-bench-state:'+Buffer.from(JSON.stringify(state)).toString('base64')))];
 await next.execute();assert.equal(next.errors.length,0);assert.equal(next.writes.length,1);assert.equal(readState(next.writes[0][1].body).slots.length,12);assert.ok(next.requests.length<=32);
 assert.equal(next.requests.filter(([route])=>route==='GET /repos/{owner}/{repo}/actions/runs/{run_id}').length,14);
});
test('reusable invocation from another workflow cannot publish with caller authority',async()=>{
 const f=fixture();f.env.WORKFLOW_REF='owner/repo/.github/workflows/other.yml@refs/heads/master';await f.execute();assert.equal(f.writes.length,0);assert.equal(f.requests.length,0);
});
