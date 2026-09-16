// Optional real-browser E2E. Synthetic files, isolated server, headless browser only.
const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const path = require('node:path');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.PEG_TEST_PYTHON || path.join(root, '.venv', process.platform==='win32'?'Scripts/python.exe':'bin/python');
const child = spawn(python, ['-u', '-m', 'tests.review_browser_fixture'], {cwd:root, stdio:['pipe','pipe','pipe']});
let browser;
(async()=>{
  const url = await new Promise((resolve,reject)=>{
    const reader = createInterface({input:child.stdout});
    const timeout=setTimeout(()=>reject(Error('fixture timed out')),20000);
    reader.once('line',line=>{clearTimeout(timeout);reader.close();resolve(JSON.parse(line).url);});
    child.once('exit',code=>reject(Error('fixture exited '+code)));
    child.stderr.on('data',data=>process.stderr.write(data));
  });
  browser = await chromium.launch({headless:true,...(process.env.PEG_TEST_BROWSER_CHANNEL?{channel:process.env.PEG_TEST_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const check=async(selector,text)=>{await page.locator(selector).filter({hasText:text}).waitFor();};
  await page.goto(url);
  await check('#conflict-count','1');
  assert.equal(await page.locator('.group').count(),1); // Verified facts start folded.
  await page.getByRole('button',{name:'采用 320 g',exact:true}).click();
  await check('#conflict-count','0');
  await page.locator('#undo').click();
  await check('#conflict-count','1');
  await page.getByRole('button',{name:'修改',exact:true}).click();
  await page.locator('#edit-value').fill('310g');
  await fs.mkdir(path.join(root,'.runtime'),{recursive:true});
  await page.screenshot({path:path.join(root,'.runtime','simple-review-edit.png'),fullPage:true});
  await page.locator('#save-edit').click();
  await page.locator('#edit-dialog').waitFor({state:'hidden'});
  await check('#conflict-count','0');
  await page.getByRole('button',{name:'已确认',exact:true}).click();
  assert.equal(await page.locator('.group').count(),2);
  await check('#groups','310');
  await page.locator('.nav[data-view=handoff]').click();
  await page.locator('#select-usable').click();
  assert.equal(await page.locator('.approved-row input:checked').count(),2);
  await page.locator('#authorize').click();
  await check('#bundles','WorkBuddy');
  await page.locator('#draft').fill('净重：310g。噪声：45dB');
  await page.locator('#check').click();
  await check('#check-result','覆盖字段一致');
  await page.locator('.nav[data-view=review]').click();
  await page.locator('#undo').click();
  await check('#conflict-count','1');
  await page.locator('#handle').click();
  await page.getByRole('button',{name:'本次不使用',exact:true}).click();
  await check('#conflict-count','0');
  await page.locator('#undo').click();
  await check('#conflict-count','1');
  for(const width of [1440,1024,768,390]){
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow at '+width);
  }
  await page.screenshot({path:path.join(root,'.runtime','simple-review-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:path.join(root,'.runtime','simple-review-wide.png'),fullPage:true});
  // Large synthetic state: do not eagerly build thousands of cards or the hidden
  // handoff view. Filtering and "show more" must still expose every fact.
  const large=await page.evaluate(()=>structuredClone(state));
  const template=large.product.facts.find(g=>g.fact_status==='verified');
  const candidate=large.product.candidates.find(c=>template.candidate_ids.includes(c.candidate_id));
  large.product.candidates=Array.from({length:6000},(_,i)=>({...candidate,candidate_id:'large-c-'+i}));
  large.product.facts=large.product.candidates.map((c,i)=>({...template,group_id:'large-g-'+i,candidate_ids:[c.candidate_id]}));
  large.available_facts=large.product.candidates;
  large.counts={...large.counts,conflicts:0,pending_facts:0,verified_facts:6000,candidates:6000};
  large.showing_previous_result=true;
  large.coverage={status:'partial',message:'本次更新未完成，下面保留的是上次结果。',can_retry:true};
  const bulk=await browser.newPage({viewport:{width:1440,height:1000}});
  bulk.on('pageerror',e=>errors.push(e.message));
  let releaseState;
  const stateGate=new Promise(resolve=>{releaseState=resolve;});
  await bulk.route('**/api/state',async route=>{await stateGate;await route.fulfill({json:large});});
  await bulk.goto(url);
  // Navigation while the first (large) response is still loading must be safe.
  await bulk.locator('.nav[data-view=handoff]').click();
  await bulk.locator('.nav[data-view=review]').click();
  releaseState();
  await bulk.locator('#confirmed-count').filter({hasText:'6000'}).waitFor();
  await bulk.getByRole('button',{name:'已确认',exact:true}).click();
  assert.equal(await bulk.locator('.group').count(),50);
  assert.equal(await bulk.locator('.approved-row').count(),0);
  assert.match(await bulk.locator('#phase').textContent(),/当前显示上次结果/);
  await bulk.getByRole('button',{name:'继续处理',exact:true}).waitFor();
  await bulk.getByRole('button',{name:'显示更多',exact:true}).click();
  assert.equal(await bulk.locator('.group').count(),100);
  await bulk.locator('#source-filter').selectOption(candidate.source_file);
  assert.equal(await bulk.locator('.group').count(),50);
  await bulk.close();
  assert.deepEqual(errors,[]);
  console.log('Headless review E2E passed: adopt, edit, undo, skip, authorize, content check, four widths, 6000-fact paging, loading-time navigation and failed-update notice.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  child.stdin.end('\n');
});
