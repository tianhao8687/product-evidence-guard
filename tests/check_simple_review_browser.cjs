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
  assert.deepEqual(errors,[]);
  console.log('Headless review E2E passed: adopt, edit, undo, skip, authorize, content check and four widths.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  child.stdin.end('\n');
});
