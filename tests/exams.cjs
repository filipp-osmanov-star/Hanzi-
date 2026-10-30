const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:390,height:844},colorScheme:'dark'});page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 const html=base.replace('\nrender();\nif(BOOT','\nwindow.__examQA={data:EXAM_DATA,store:examLoad,items:examItems,score:examScore,normalise:examNorm};\nrender();\nif(BOOT');
 await page.route('**/*',r=>r.request().url()==='http://127.0.0.1:8878/'?r.fulfill({contentType:'text/html',body:html}):r.abort());
 await page.goto('http://127.0.0.1:8878/');
 await page.evaluate(()=>{localStorage.setItem('cc2',JSON.stringify({'你':{s:'know',n:7}}));localStorage.setItem('cc2-hanzi-elements-v1',JSON.stringify({'radicals:96':{s:'mix'}}));});await page.reload();
 const nav=async tab=>{await page.locator('#menu-toggle').click();await page.locator(`[data-tb="${tab}"]`).click();};
 const scanEnglish=async()=>{const lines=(await page.locator('body').innerText()).split('\n').filter(s=>/[А-Яа-яЁё]/.test(s)&&s.trim()!=='Русский');assert.deepEqual(lines,[]);};
 await page.locator('#lesson-exam-link').click();assert.equal(await page.locator('.exam-section').count(),8);assert.equal(await page.locator('#course-bar').isVisible(),false);
 await page.locator('#language-switch').click();await scanEnglish();await page.locator('.exam-source summary').click();await scanEnglish();
 await page.locator('[data-exam-section="1"]').click();await page.locator('#exam-answer').fill('发音');await page.locator('#exam-check').click();assert.match(await page.locator('#exam-feedback').innerText(),/Correct/);
 await page.locator('#exam-next').click();await page.locator('#exam-answer').fill('xiao4 zhang3');await page.locator('#exam-check').click();assert.match(await page.locator('#exam-feedback').innerText(),/Correct/);await scanEnglish();
 await page.locator('#exam-edit').click();await page.locator('#exam-answer').fill('xiao zhang');await page.locator('#exam-check').click();assert.match(await page.locator('#exam-feedback').innerText(),/Check your answer/);
 await page.locator('#exam-home').click();await page.locator('#exam-full').click();await page.locator('#exam-cancel').click();assert.equal(await page.evaluate(()=>window.__examQA.store().attempt.mode),'practice');await page.locator('#exam-full').click();await page.locator('#exam-replace').click();
 const data=await page.evaluate(()=>window.__examQA.data);assert.equal(data.questions.length,61);assert.equal(data.questions.reduce((n,q)=>n+q.points,0),100);assert.equal(data.questions.filter(q=>q.image).length,10);
 const pinyin=await page.evaluate(()=>['xiao4 zhang3','dian4 hua4','rong2 yi4','duo1 shao5','bie2 de5'].map(x=>window.__examQA.normalise(x,true)));assert.deepEqual(pinyin,['xiàozhǎng','diànhuà','róngyì','duōshao','biéde']);
 for(let i=0;i<data.questions.length;i++){
  const q=data.questions[i];
  if(q.options){const answer=q.id==='4-1'?'个':q.answer[0];await page.locator(`[data-exam-option="${q.options.indexOf(answer)}"]`).click();}
  else if(q.type==='order'){
   const normal=q.answer[0].replace(/[。？！]/g,'');let built='';const used=new Set();while(built!==normal){const j=q.tokens.findIndex((t,j)=>!used.has(j)&&normal.startsWith(built+t));assert(j>=0,'Find next token '+q.id);await page.locator(`[data-exam-token="${j}"]`).click();used.add(j);built+=q.tokens[j];}
  }else await page.locator('#exam-answer').fill(q.id==='1-2'?'xiao4 zhang3':q.answer[0]);
  assert.equal(await page.locator('#exam-feedback').innerText(),'','Exam mode must not reveal answers');
  if(q.id==='1-9'){
   await page.reload();await nav('exams');await page.locator('#exam-resume').click();assert.equal(await page.locator('#exam-answer').inputValue(),'认识');
  }
  if(q.type==='picture')assert(await page.locator('.exam-image img').evaluate(el=>el.complete&&el.naturalWidth>0));
  if(['1-2','3-7','5-4','6-3','7-6','8-1'].includes(q.id)){
   await scanEnglish();for(const width of [320,390,768]){await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No overflow at '+width+' '+q.id);}await page.setViewportSize({width:390,height:844});
  }
  await page.locator('#exam-next').click();
 }
 await page.locator('#exam-submit').click();assert.match(await page.locator('#exam-result-score').innerText(),/64 \/ 65/);assert.match(await page.locator('#exam-result-score').innerText(),/0 \/ 11 assessed/);await scanEnglish();
 await page.locator('#exam-errors').click();assert.match(await page.locator('#exam-feedback').innerText(),/碗/);assert.match(await page.locator('#exam-feedback').innerText(),/handwritten 个 is incorrect/);assert.equal(await page.locator('.exam-selected').innerText(),'A. 个');
 await page.locator('#exam-home').click();await page.locator('#exam-resume').click();await page.locator('#exam-pending').click();
 for(const q of data.questions.filter(q=>['manual','essay'].includes(q.type))){
  await page.locator('#exam-jump').selectOption(String(data.questions.findIndex(x=>x.id===q.id)));await page.locator(`[data-exam-self="${q.id}"]`).selectOption(String(q.points));await scanEnglish();
 }
 await page.locator('#exam-next').click();assert.match(await page.locator('#exam-result-score').innerText(),/99 \/ 100/);assert.match(await page.locator('#exam-result-score').innerText(),/11 \/ 11 assessed/);
 await page.reload();await nav('exams');await page.locator('#exam-resume').click();assert.match(await page.locator('#exam-result-score').innerText(),/99 \/ 100/);
 assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('cc2'))),{'你':{s:'know',n:7}});assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('cc2-hanzi-elements-v1'))),{'radicals:96':{s:'mix'}});
 await page.locator('#language-switch').click();assert.match(await page.locator('#exam-result-score').innerText(),/Учебный итог/);await page.locator('#exam-home').click();
 const out=process.env.HANZI_SCREENSHOT_DIR;if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'exam-home.png'),fullPage:true});}
 await page.locator('[data-exam-section="3"]').click();if(out)await page.screenshot({path:path.join(out,'exam-picture.png'),fullPage:true});
 // Empty attempts can be finished; manual answers are not auto-awarded points.
 await page.locator('#exam-finish').click();await page.locator('#exam-submit').click();assert.match(await page.locator('#exam-result-score').innerText(),/0 \/ 10/);
 // Unanswered open questions must stay at zero, with no self-awarded points.
 await page.locator('#exam-home').click();await page.locator('[data-exam-section="6"]').click();await page.locator('#exam-finish').click();await page.locator('#exam-submit').click();assert.match(await page.locator('#exam-result-score').innerText(),/0 \/ 10/);assert.match(await page.locator('#exam-result-score').innerText(),/5 \/ 5/);await page.locator('#exam-review').click();assert.equal(await page.locator('[data-exam-self]').isDisabled(),true);
 // Corrupt local state must not break the rest of the app.
 await page.evaluate(()=>localStorage.setItem('cc2-exams-v1','broken'));await page.reload();await nav('exams');assert.match(await page.locator('#exam-save-status').innerText(),/Не удалось/);await nav('dict');await page.locator('#q').fill('你');assert(await page.locator('.item').count()>0);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: all 61 tasks, source pictures, tone-sensitive pinyin, 100-point scoring, exam/practice modes, restart confirmation, 11 self-assessments, reload/resume, RU/EN, 320/390/768px, corrupt storage, legacy progress untouched.');
})().catch(e=>{console.error(e);process.exit(1);});
