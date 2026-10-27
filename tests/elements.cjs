const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:390,height:844}});page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8').replace('\nrender();\nif(BOOT','\nwindow.__elementsQA={state:elementState,data:HANZI_ELEMENTS,app:S,render};\nrender();\nif(BOOT');
 await page.route('**/*',r=>r.request().url()==='http://127.0.0.1:8877/'?r.fulfill({contentType:'text/html',body:html}):r.abort());
 await page.goto('http://127.0.0.1:8877/');
 // Seed legacy word progress, then exercise the new feature without touching it.
 await page.evaluate(()=>localStorage.setItem('cc2',JSON.stringify({'你':{s:'know',n:7}})));await page.reload();
 const nav=async tab=>{await page.locator('#menu-toggle').click();await page.locator(`[data-tb="${tab}"]`).click();};
 await nav('strokes');
 assert.equal(await page.locator('.element-tile').count(),19);assert.equal(await page.locator('#course-bar').isVisible(),false);
 assert.equal(await page.locator('.element-tile svg').count(),19);
 await page.locator('[data-element="1"]').click();await page.locator('#element-animate').click();await page.locator('.stroke-animation').waitFor();
 await page.locator('[data-element-mark="know"]').click();
 assert.match(await page.locator('#progress').innerText(),/1 \/ 19/);
 await page.locator('#element-write').click();
 await page.locator('#element-canvas').waitFor();
 const bounds=await page.locator('#element-canvas').boundingBox();await page.mouse.move(bounds.x+20,bounds.y+40);await page.mouse.down();await page.mouse.move(bounds.x+180,bounds.y+80);await page.mouse.up();
 await page.locator('#element-undo').click();await page.locator('#element-clear').click();await page.locator('#element-guide').click();
 await page.locator('#element-back').click();
 await page.locator('[data-emode="study"]').click();
 await page.locator('#element-search').fill('heng');assert.ok(await page.locator('.element-tile').count()>1);await page.locator('#element-search').fill('');
 await page.locator('#element-filter').selectOption('know');assert.equal(await page.locator('.element-tile').count(),1);await page.locator('#element-filter').selectOption('all');
 await page.locator('[data-emode="quiz"]').click();
 for(let i=0;i<10;i++){
 const answer=await page.evaluate(()=>{const q=window.__elementsQA.state.strokes.quiz;return q.options.findIndex(e=>e.id===q.items[q.i].id);});
 await page.locator(`[data-element-answer="${i===0?(answer+1)%4:answer}"]`).click();
 assert.equal(await page.locator('[data-element-answer]:disabled').count(),4);await page.locator('#element-next').click();
 }
 await page.getByRole('heading',{name:'Результат: 9 / 10'}).waitFor();await page.locator('#element-errors').click();assert.match(await page.locator('.element-quiz').innerText(),/Вопрос 1 \/ 1/);
 await nav('radicals');assert.equal(await page.locator('.element-tile').count(),214);
 await page.locator('#element-count').selectOption('1');assert.equal(await page.locator('.element-tile').count(),6);await page.locator('#element-count').selectOption('0');
 await page.locator('#element-search').fill('нефрит');assert.equal(await page.locator('.element-tile').count(),1);await page.locator('[data-element="96"]').click();assert.match(await page.locator('.element-detail').innerText(),/王/);
 await page.locator('[data-element-mark="mix"]').click();await page.locator('#element-back').click();await page.locator('#element-search').fill('');await page.locator('#element-filter').selectOption('review');assert.equal(await page.locator('.element-tile').count(),1);
 await page.locator('#element-filter').selectOption('all');
 const integrity=await page.evaluate(()=>({strokes:window.__elementsQA.data.strokes.map(e=>[e.id,e.path,e.note]),radicals:window.__elementsQA.data.radicals.map(e=>[e.id,e.h,e.p,e.r,e.strokes]),progress:JSON.parse(localStorage.getItem('cc2')),elements:JSON.parse(localStorage.getItem('cc2-hanzi-elements-v1'))}));
 assert.deepEqual(integrity.progress,{'你':{s:'know',n:7}});assert.equal(integrity.elements['radicals:96'].s,'mix');
 assert.deepEqual(integrity.radicals.map(e=>e[0]),Array.from({length:214},(_,i)=>i+1));assert.equal(new Set(integrity.radicals.map(e=>e[1])).size,214);
 for(const e of integrity.strokes)assert.ok(e[1].startsWith('M')&&e[2]);for(const e of integrity.radicals)assert.ok(e[2]&&e[3]&&e[4]>=1&&e[4]<=17);
 await page.reload();await nav('radicals');await page.locator('[data-element="96"]').click();assert.equal(await page.locator('[data-element-mark="mix"]').getAttribute('aria-pressed'),'true');
 await nav('lessons');assert.equal(await page.locator('#course-bar').isVisible(),true);await page.locator('#course').selectOption('hsk2');await page.locator('.lesson-card').first().waitFor();assert.match(await page.locator('#progress').innerText(),/497/);
 await nav('dict');await page.locator('#q').fill('你');assert.ok(await page.locator('.item').count()>0);
 await nav('cards');await page.locator('#op').click();await page.locator('[data-m=know]').waitFor();
 await nav('quiz');await page.locator('[data-a="0"]').click();await page.locator('#nx').click();
 await nav('sent');await page.locator('[data-p]').first().click();assert.equal(await page.locator('[data-u]').count(),1);
 await nav('write');await page.locator('#gd').click();await page.locator('#cl').click();
 await nav('info');await page.getByRole('heading',{name:'Черты и ключи',exact:true}).waitFor();
 await nav('strokes');for(const width of [360,390,768]){await page.setViewportSize({width,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);}
 await page.setViewportSize({width:390,height:844});const out=process.env.HANZI_SCREENSHOT_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'strokes.png'),fullPage:true,animations:'disabled'});await page.locator('[data-element="1"]').click();await page.screenshot({path:path.join(out,'stroke-detail.png'),fullPage:true,animations:'disabled'});await nav('radicals');await page.screenshot({path:path.join(out,'radicals.png'),fullPage:false,animations:'disabled'});}
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: 19 stroke diagrams, 214 radicals, details/variants, animation, writing/undo, search/filters, 10-question quiz and error retry, saved progress, legacy data, HSK navigation, responsive layout; no JS errors.');
})().catch(e=>{console.error(e);process.exit(1)});
