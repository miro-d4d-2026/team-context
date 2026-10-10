import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {continuousMonitorPresentation,REVIEW_LABELS,sourceText,profileText,escapeLearning,candidateStatus,reviewPayload,intervalPayload,labelRows,captureCaption,candidateMarkup,sessionSignature,currentFeedbackLabel,runPresentation,experimentField,experimentHistory,experimentChart,durationText,modelLearningFacts,workflowSummary,reportWindowCaption,latestReportId,currentModelMarkup,storedMonitorSummary,storedMonitorMarkup,reviewedReportSummary,learningCohorts,reviewedRecordCaption,trainingSummaryMarkup,suggestedCaptureCondition,captureConditionValue,capturePayload,autoTrainingPayload,autoTrainingBlocker,autoPlanKey,autoPlanCaption,candidateTrainingCounts,excludedTrainingMarkup} from './learning.mjs';
const meta={asset_id:'GEN-1',installation_id:'phone-1',source_kind:'hardware',profile:'vibration',condition:'same',sensor_config:{odr:208,range:4}};
const session=(id,review,role)=>({id,review,role,ended:20,started:1,count:40,meta:{...meta,label:id}});
const normal=Array.from({length:4},(_,i)=>session('n'+(i+1),'normal','none'));
test('five human labels and segmented state remain independent labels',()=>{assert.equal(REVIEW_LABELS.abnormal,'비정상 확인');assert.equal(REVIEW_LABELS.exclude,'측정 오류 / 제외');assert.equal(REVIEW_LABELS.segmented,'구간별 라벨');});
test('signature ignores display label and metadata key order',()=>{assert.equal(sessionSignature(normal[0]),sessionSignature({...normal[0],meta:{...meta,sensor_config:{range:4,odr:208},label:'new name'}}));});
test('review edit requires author, grounds and a known human label',()=>{assert.throws(()=>reviewPayload('1',{status:'normal',reviewer:'',note:'yes'}));assert.throws(()=>reviewPayload('1',{status:'normal',reviewer:'우지',note:' '}));assert.throws(()=>reviewPayload('1',{status:'whatever',reviewer:'우지',note:'yes'}));assert.deepEqual(reviewPayload('1',{status:'abnormal',reviewer:' 우지 ',note:' 강도 증가 ',fault_kind:'자극 변화'}),{session_id:'1',status:'abnormal',reviewer:'우지',note:'강도 증가',fault_kind:'자극 변화'});});
test('interval requires positive ordered observed-time bounds and a real label',()=>{const review={reviewer:'우지',note:'기준 진동'};assert.throws(()=>intervalPayload('s',{start_s:'10',end_s:'2',label:'normal'},review));assert.throws(()=>intervalPayload('s',{start_s:'0',end_s:'2',label:'unreviewed'},review));assert.deepEqual(intervalPayload('s',{start_s:'2',end_s:'8',label:'normal'},review),{session_id:'s',label:'normal',start_s:2,end_s:8,reviewer:'우지',note:'기준 진동'});});
test('invalidated or failed model cannot activate; active is separate from passed',()=>{assert.equal(candidateStatus({passed:true,labels_current:false}).canActivate,false);assert.equal(candidateStatus({passed:false}).canActivate,false);assert.equal(candidateStatus({passed:true,active:true}).canActivate,false);assert.equal(candidateStatus({passed:true,active:false,labels_current:true}).canActivate,true);});
test('candidate UI shows validation coverage not fabricated fault probability',()=>{const output=candidateMarkup({id:'m<1',passed:true,validation:{false_positive_rate:.02,abnormal_recall:.8,coverage:.96},signature:meta},'feedback',true);assert.match(output,/2.0%/);assert.match(output,/80.0%/);assert.match(output,/실제 고장 확률 아님/);assert.match(output,/disabled/);assert.match(output,/m&lt;1/);});
test('source text leaves missing provenance unknown',()=>{assert.equal(sourceText({}),'출처 확인 필요');assert.equal(sourceText({source_kind:'hardware'}),'실측 IMU');assert.equal(sourceText({source_kind:'synthetic'}),'모의 데이터');assert.equal(profileText({profile:'vibration'}),'진동 전용');});
test('label rows preserve withdrawn labels rather than silently hide',()=>{const rows=[{id:'1',active:0}];assert.deepEqual(labelRows({labels:{labels:rows}}),rows);assert.deepEqual(labelRows({labels:rows}),rows);});
test('live capture caption never borrows baseline ID as a new pinned model',()=>{const output=captureCaption({live:true,session:{count:15,meta:{label:'기준 실험'}}});assert.match(output,/새 정상 기준 미적용/);assert.match(output,/라벨 분류 미적용/);});
test('arbitrary names and notes are escaped',()=>{assert.equal(escapeLearning('<script>"&'), '&lt;script&gt;&quot;&amp;');});

test('stale result never retains a positive classifier statement as current',()=>{assert.match(currentFeedbackLabel({live:true,result:{state:'unknown',feedback:{state:'normal_like'}}}),/판단 보류/);assert.match(currentFeedbackLabel({live:true,age_s:7,result:{state:'normal',feedback:{state:'normal_like'}}}),/판단 보류/);assert.match(currentFeedbackLabel({live:true,result:{state:'normal',feedback:{state:'normal_like'}}},true),/판단 보류/);assert.equal(currentFeedbackLabel({live:true,age_s:1,result:{state:'normal',feedback:{state:'normal_like'}}}),'검토된 정상과 유사');});
test('different dataset cohort is not silently combined and public source is explicit',()=>{assert.notEqual(sessionSignature({...normal[0],meta:{...meta,dataset_id:'one'}}),sessionSignature({...normal[0],meta:{...meta,dataset_id:'two'}}));assert.equal(sourceText({source_kind:'public'}),'공개 데이터');});

test('synthetic learning run never appears beside the unrelated dashboard monitor',()=>{assert.equal(runPresentation({live:true,session:{meta:{source_kind:'synthetic'}}}),'experiment');assert.equal(runPresentation({live:true,session:{meta:{source_kind:'hardware'}}}),'dashboard');assert.equal(runPresentation({live:false,session:{meta:{source_kind:'synthetic'}}}),'none');assert.equal(runPresentation({live:true,session:{meta:{source_kind:'recorded'}}}),'none');});
test('experiment chart uses its own frame/evidence pair and rejects future windows',()=>{const one={frame:{elapsed_s:2,quality:'ok',values:{vibration_rms_g:.05}},result:{evidence:[{key:'vibration_rms_g',expected:.04}]}},later={frame:{elapsed_s:4,quality:'ok',values:{vibration_rms_g:.9}},result:{evidence:[]}};const s={history:[one,later],latest:one};assert.deepEqual(experimentHistory(s,'vibration_rms_g'),[{time:2,actual:.05,expected:.04}]);assert.match(experimentChart(s,'vibration_rms_g'),/별도 합성 실험/);});
test('invalid experiment input is a gap and missing expectation is not invented',()=>{const rows=[{frame:{elapsed_s:2,quality:'ok',values:{vibration_rms_g:.05}},result:{}},{frame:{elapsed_s:4,quality:'invalid',values:{vibration_rms_g:.07}},result:{evidence:[{key:'vibration_rms_g',expected:.04}]}}];assert.deepEqual(experimentHistory({history:rows},'vibration_rms_g'),[{time:2,actual:.05,expected:null},{time:4,actual:null,expected:null}]);assert.equal(experimentHistory({history:rows},'fake').length,0);});
test('experiment scenario selects the relevant observed metric',()=>{assert.equal(experimentField('pressure'),'oil_bar');assert.equal(experimentField('intermittent'),'envelope_cv');assert.equal(experimentField('vibration'),'vibration_rms_g');});


test('learning evidence uses actual trained window count and runtime, not stored session sizes',()=>{
 const facts=modelLearningFacts({splits:{fit:['a','b','b'],calibration:['c'],validation:['d']},core_phase:{fit_count:570},validation:{total_windows:300},training_seconds:.512},'normal');
 assert.equal(facts.fitRuns,2);assert.equal(facts.fitWindows,570);assert.equal(facts.validationWindows,300);assert.equal(facts.trainingTime,'0.51초');
 const classifier=modelLearningFacts({splits:{fit:['a','b']},validation:{normal_windows:240,abnormal_windows:260},training_seconds:1.356},'feedback');
 assert.equal(classifier.fitWindows,null);assert.equal(classifier.validationWindows,500);assert.equal(classifier.trainingTime,'1.4초');
 assert.equal(durationText(undefined),'미기록');assert.equal(durationText(-1),'미기록');
});
test('candidate learning details do not turn missing counts into zero training claims',()=>{
 const output=candidateMarkup({id:'fb-example',passed:true,signature:{source_kind:'synthetic'},splits:{fit:['a','b'],calibration:['c'],validation:['d']},validation:{total_windows:600},training_seconds:.8},'feedback',false);
 assert.match(output,/모의 데이터/);assert.match(output,/정상·비정상 학습/);assert.match(output,/600개/);assert.match(output,/0.80초/);assert.doesNotMatch(output,/학습 분석 창/);
});
test('synthetic active ML candidates never imply live IMU was trained',()=>{
 const snapshot={sessions:normal,models:[{active:true,passed:true,signature:{source_kind:'synthetic'}}],feedback_models:[{active:true,passed:true,signature:{source_kind:'synthetic'}}],preserved_baseline:{baseline_id:'old-real-baseline',state:'ready'}};
 const summary=workflowSummary(snapshot);assert.equal(summary.activeNormal,0);assert.equal(summary.activeClassifier,0);assert.match(summary.model,/진동 정상 기준 적용/);assert.match(summary.model,/검토 라벨 학습 대기/);
});
test('pending and invalidated real models are not advertised as applied models',()=>{
 const real=active=>({active,passed:true,signature:{source_kind:'hardware'}}),snapshot={models:[real(false)],feedback_models:[{...real(true),labels_current:false}]};
 const summary=workflowSummary(snapshot);assert.equal(summary.activeNormal,0);assert.equal(summary.activeClassifier,0);assert.match(summary.model,/실측 후보 2개/);
 const active=workflowSummary({models:[real(true)],feedback_models:[real(true)]});assert.equal(active.activeNormal,1);assert.equal(active.activeClassifier,1);
});
test('workflow prioritizes ended human review and current recording controls',()=>{
 const snapshot={sessions:[...normal,session('pending','unreviewed','none'),{...session('live','unreviewed','none'),ended:null}]};
 const summary=workflowSummary(snapshot);assert.equal(summary.closedRuns,5);assert.equal(summary.reviewWaiting,1);assert.equal(summary.next.href,'#records');assert.equal(summary.button,'보고서 기록 시작');
 assert.equal(workflowSummary({...snapshot,live:true,session:{meta:{source_kind:'hardware'}}}).button,'보고서 기록 종료');
 assert.equal(workflowSummary({...snapshot,live:true,session:{meta:{source_kind:'synthetic'}}}).button,'합성 운전 확인');
 assert.match(workflowSummary(snapshot,true).summary,/마지막 기록/);
});
test('report defaults to latest ended run while respecting explicit selection',()=>{
 const rows=[{id:'running',started:30},{id:'old',started:10,ended:11},{id:'latest',started:20,ended:22}];
 assert.equal(latestReportId(rows),'latest');assert.equal(latestReportId(rows,'old'),'old');assert.equal(latestReportId(rows,'running'),'running');assert.equal(latestReportId([{id:'only-running',started:30}]),'');
});
test('long report distinguishes saved windows from bounded analysis',()=>{
 assert.equal(reportWindowCaption({count:2500},{analysed_windows:2000}),'저장 2500개 · 보고서 분석 2000개');
 assert.equal(reportWindowCaption({count:50},{analysed_windows:50}),'50개');assert.equal(reportWindowCaption({},{}),'—');
});


test('existing ready baseline is shown without inventing reviewed-label training',()=>{
 const s={preserved_baseline:{baseline_id:'imu-old',state:'ready'}};const markup=currentModelMarkup(s);
 assert.match(markup,/정상 진동으로 학습한 기준/);assert.match(markup,/imu-old/);assert.match(markup,/새 검토 모델은 별도로 학습/);
 assert.match(workflowSummary(s).model,/진동 정상 기준 적용 · 검토 라벨 학습 대기/);
 assert.doesNotMatch(workflowSummary({preserved_baseline:{baseline_id:'imu-old',state:'pending'}}).model,/진동 정상 기준 적용/);
});
test('registered reviewed models retain provenance and exclude invalidated candidates',()=>{
 const m={id:'synthetic-label',active:true,passed:true,labels_current:true,signature:{source_kind:'synthetic'}};
 const markup=currentModelMarkup({feedback_models:[m,{...m,id:'invalidated',labels_current:false}]});
 assert.match(markup,/모의 데이터 · 라벨 분류 · 적용 중/);assert.match(markup,/synthetic-label/);assert.doesNotMatch(markup,/invalidated/);
});
test('disconnected current model panel never asserts current baseline application',()=>{
 const s={preserved_baseline:{baseline_id:'imu-old',state:'ready'}};
 assert.match(currentModelMarkup(s,true),/연결 확인 필요/);assert.doesNotMatch(currentModelMarkup(s,true),/진동 기준 적용/);
 assert.equal(workflowSummary(s,true).model,'분석 장치 연결 확인 필요');
});


test('report keeps archived original baseline distinct from absent reviewed model',()=>{
 const detail={session:{model_id:null,feedback_model_id:null},report:{title:'정상 기준 등록이 필요합니다'},frames:[{result:{existing_monitor:{window_id:'a',baseline_id:'imu-original',pattern:'idle',state:'normal',vibration:{state:'normal',summary:'정지 수준 진동'}}}},{result:{existing_monitor:{window_id:'b',baseline_id:'imu-original',pattern:'idle',state:'normal',vibration:{state:'normal'}}}}]};
 assert.deepEqual(storedMonitorSummary(detail),{observedWindows:2,warningWindows:0,unknownWindows:0,baselineIds:['imu-original'],patterns:{'정지 수준':2},title:'주의 판정 없음',tone:'normal',summary:'당시 저장된 상태와 패턴을 확인하세요.'});
 assert.match(storedMonitorMarkup(detail),/기존 진동 기준 · 당시 판단/);assert.match(storedMonitorMarkup(detail),/imu-original/);assert.equal(reviewedReportSummary(detail).title,'검토 모델 미적용');
});
test('old imported recordings never borrow current baseline as past evidence',()=>{
 const detail={session:{count:48},preserved_baseline:{baseline_id:'current-only',state:'ready'},frames:[{result:{state:'reference'}}]};
 assert.equal(storedMonitorSummary(detail),null);assert.equal(storedMonitorMarkup(detail),'');assert.equal(reviewedReportSummary(detail).title,'검토 모델 미적용');
});
test('archived warning summary retains earlier warning after recovery and counts unique windows',()=>{
 const first={window_id:'a',baseline_id:'old-A',pattern:'strong',state:'inspect',vibration:{state:'inspect',summary:'강한 진동 점검'}};
 const recovered={window_id:'b',baseline_id:'old-B',pattern:'normal',state:'normal',vibration:{state:'normal',summary:'기준 진동'}};
 const detail={frames:[{result:{existing_monitor:first}},{result:{existing_monitor:first}},{result:{existing_monitor:recovered}}]};
 const summary=storedMonitorSummary(detail);assert.equal(summary.observedWindows,2);assert.equal(summary.warningWindows,1);assert.equal(summary.summary,'강한 진동 점검');assert.deepEqual(summary.baselineIds,['old-A','old-B']);assert.equal(summary.patterns['강한 진동'],1);
});
test('original vibration report ignores unrelated simulated telemetry alarms and preserves unknowns',()=>{
 const detail={frames:[{result:{existing_monitor:{window_id:'a',state:'urgent',pattern:'normal',vibration:{state:'normal',summary:'기준 진동'}}}},{result:{existing_monitor:{window_id:'b',state:'unavailable'}}}]};
 const summary=storedMonitorSummary(detail);assert.equal(summary.warningWindows,0);assert.equal(summary.unknownWindows,1);assert.equal(summary.tone,'unavailable');assert.match(summary.title,/판단 보류/);
});
test('stored monitor source text is escaped and feedback-only reports do not ask for existing baseline',()=>{
 const detail={frames:[{result:{existing_monitor:{baseline_id:'<script>',vibration:{state:'normal',summary:'<alert>'}}}}],session:{feedback_model_id:'fb-1'},report:{title:'정상 기준 등록이 필요합니다',feedback_flagged_windows:0}};
 const markup=storedMonitorMarkup(detail);assert.match(markup,/&lt;script&gt;/);assert.match(markup,/&lt;alert&gt;/);assert.equal(reviewedReportSummary(detail).title,'검토 라벨 분류로 분석한 기록');
});


test('simple human review omits ML split role while retaining author and rationale',()=>{
 const body=reviewPayload('run-1',{status:'abnormal',role:'validation',reviewer:'우지',note:'강한 진동 확인'});
 assert.equal(body.status,'abnormal');assert.equal(Object.hasOwn(body,'role'),false);assert.equal(body.note,'강한 진동 확인');
});
test('automatic training cohorts keep actual, synthetic and installation conditions separate',()=>{
 const rows=[session('real-normal','normal','none'),session('real-abnormal','abnormal','none'),{...session('mock','normal','none'),meta:{...meta,source_kind:'synthetic'}},{...session('moved','normal','none'),meta:{...meta,installation_id:'new-position'}},{...session('other-load','normal','none'),meta:{...meta,condition:'changed'}}];
 const cohorts=learningCohorts(rows);assert.equal(cohorts.length,4);const main=cohorts.find(c=>c.eligible.length===2);assert.equal(main.normal,1);assert.equal(main.abnormal,1);assert.deepEqual(autoTrainingPayload(main),{kind:'auto',session_ids:['real-normal','real-abnormal']});
});
test('automatic record eligibility excludes unreviewed, held, excluded and current runs',()=>{
 const rows=[session('n','normal','none'),session('a','abnormal','none'),session('s','segmented','none'),session('h','uncertain','none'),session('x','exclude','none'),session('u','unreviewed','none'),{...session('running','normal','none'),ended:null}];
 const c=learningCohorts(rows)[0];assert.deepEqual(c.eligible.map(s=>s.id),['n','a','s']);assert.equal(c.held,2);assert.equal(c.waiting,1);assert.equal(c.segmented,1);assert.throws(()=>autoTrainingPayload(null));
});
test('one AI learning action respects server readiness and explains normal-only learning',()=>{
 const c=learningCohorts(normal)[0];assert.match(autoTrainingBlocker({},c,null,true),/확인하고/);assert.equal(autoTrainingBlocker({},c,{ready:true}),null);assert.equal(autoTrainingBlocker({},c,{ready:false,reason:'독립 운전 추가 필요'}),'독립 운전 추가 필요');
 assert.match(autoTrainingBlocker({live:true},c,{ready:true}),/종료/);assert.match(autoTrainingBlocker({job:{state:'running'}},c,{ready:true}),/학습 중/);assert.match(autoPlanCaption({ready:true,feedback:{ready:false,reason:'비정상 기록 부족'}}),/정상 패턴부터 학습/);assert.match(autoPlanCaption({ready:true,feedback:{ready:false,reason:'비정상 기록 부족'}}),/비정상 기록 부족/);assert.match(autoPlanCaption({ready:true,feedback:{ready:true}}),/정상·비정상으로 학습/);
});
test('over 30 reviewed runs has an explicit recoverable record-scope limit',()=>{
 const c=learningCohorts(Array.from({length:31},(_,i)=>session('run-'+i,'normal','none')))[0];assert.match(autoTrainingBlocker({},c,null),/30회/);assert.equal(autoTrainingBlocker({}, {...c,eligible:c.eligible.slice(0,30)}, {ready:true}),null);
});
test('automatic-plan cache changes with review revisions or model activation, not polling',()=>{
 const c=learningCohorts(normal)[0],s={models:[{id:'m',active:false,labels_current:true}]};const key=autoPlanKey(s,c);assert.equal(key,autoPlanKey(s,c));assert.notEqual(key,autoPlanKey({...s,models:[{id:'m',active:true,labels_current:true}]},c));assert.notEqual(key,autoPlanKey(s,{...c,eligible:c.eligible.map((r,i)=>i? r:{...r,review_version:2})}));
});
test('normal-only candidate never invents an abnormal detection rate',()=>{
 const m={id:'n',passed:true,auto_training:{normal_sessions:6,abnormal_sessions:0,segmented_sessions:0},validation:{outside_rate:.05,total_windows:90,coverage:1},signature:meta};
 assert.deepEqual(candidateTrainingCounts(m,'normal'),{normal:6,abnormal:0,segmented:0});const html=candidateMarkup(m,'normal',false);assert.match(html,/사용한 정상 기록 <b>6회/);assert.match(html,/5.0%/);assert.match(html,/비정상 검출률<\/span><strong>—<\/strong><small>미평가/);assert.match(html,/새 모델 적용/);
});
test('basic learning and review screens have no manual algorithm or split selector',()=>{
 const html=readFileSync(new URL('./operation.html',import.meta.url),'utf8');assert.doesNotMatch(html,/id="lw-model-kind"|name="role"|<th scope="col">학습 역할/);assert.match(html,/id="lw-train"[^>]*>AI 학습/);assert.match(html,/id="lw-training-cohort"/);assert.match(html,/id="lw-record-scope"/);assert.match(html,/id="dashboard-page"/);
});

test('excluded short or low-quality records have an escaped actionable explanation',()=>{
 const html=excludedTrainingMarkup({excluded:[{session_id:'n',label:'normal',reason:'유효 구간 30개 필요 <test>'}]},[{id:'n',meta:{label:'측정 <원본>'}}]);assert.match(html,/측정 &lt;원본&gt;/);assert.match(html,/유효 구간 30개 필요 &lt;test&gt;/);assert.match(html,/정상 확인/);
});

test('segmented reviews are visible without inventing whole-run normal or abnormal labels',()=>{
 const rows=Array.from({length:4},(_,i)=>session('seg-'+i,'segmented','none'));
 const caption=reviewedRecordCaption(rows),markup=trainingSummaryMarkup(learningCohorts(rows)[0]);
 assert.match(caption,/전체 정상 0회 · 전체 비정상 0회 · 구간 검토 4회/);
 assert.match(markup,/<span>구간 검토<\/span><strong>4<small>회/);
 assert.match(markup,/<span>전체 정상<\/span><strong>0<small>회/);
 assert.match(markup,/<span>전체 비정상<\/span><strong>0<small>회/);
});
test('review counts keep held and active runs separate from reviewed complete runs',()=>{
 const rows=[session('normal','normal','none'),session('abnormal','abnormal','none'),session('segments','segmented','none'),session('held','uncertain','none'),session('excluded','exclude','none'),{...session('running','segmented','none'),ended:null}];
 assert.match(reviewedRecordCaption(rows),/전체 정상 1회 · 전체 비정상 1회 · 구간 검토 1회/);
 const cohort=learningCohorts(rows)[0];assert.equal(cohort.held,2);assert.equal(cohort.segmented,1);
 assert.match(trainingSummaryMarkup(),/<span>구간 검토<\/span><strong>0<small>회/);
});

test('new hardware capture suggests the latest applied live vibration condition only',()=>{
 const model=(condition,created_at,change={})=>({active:true,passed:true,labels_current:true,created_at,signature:{...meta,condition,dataset_id:'',...change}});
 const state={models:[model('older hardware',10),model('mock newer',100,{source_kind:'synthetic'}),model('fusion newer',110,{profile:'fusion'}),model('imported newer',120,{dataset_id:'old-recording'})],feedback_models:[model('current hardware',20),{...model('invalidated newer',130),labels_current:false},{...model('inactive newer',140),active:false},{...model('failed newer',150),passed:false}]};
 assert.equal(suggestedCaptureCondition(state),'current hardware');assert.equal(captureConditionValue(state,'same-installation'),'current hardware');
 assert.equal(suggestedCaptureCondition({models:[model('recorded only',160,{source_kind:'recorded'})]}),'');
});
test('capture condition preserves user edits and shows the pinned condition during capture',()=>{
 const state={feedback_models:[{active:true,passed:true,created_at:20,signature:{...meta,condition:'applied model condition'}}]};
 assert.equal(captureConditionValue(state,'user changed installation',true),'user changed installation');
 assert.equal(captureConditionValue(state,'',true),'');
 assert.equal(captureConditionValue({},''),'same-installation');
 assert.equal(captureConditionValue({live:true,session:{meta:{source_kind:'hardware',condition:'running condition'}}},'same-installation'),'running condition');
});
test('capture request sends the visible operating condition rather than a fixed default',()=>{
 assert.deepEqual(capturePayload({name:' normal run ',condition:' same phone installation '}),{source_kind:'hardware',label:'normal run',condition:'same phone installation'});
 assert.throws(()=>capturePayload({name:'run',condition:' '}),/운전 조건/);
 assert.throws(()=>capturePayload({name:'run',condition:'x'.repeat(181)}),/운전 조건/);
 const html=readFileSync(new URL('./operation.html',import.meta.url),'utf8');assert.match(html,/name="condition"[^>]*value="same-installation"/);assert.match(html,/id="lw-capture-condition-help"/);
});


test('continuous model result is visible before and after report recording',()=>{
 const monitoring={fresh:true,age_s:1,model_id:'new-normal',feedback_model_id:'new-feedback',recording:false,result:{state:'attention',reason:'진동 변화',feedback:{state:'abnormal_like'}}};
 const shown=continuousMonitorPresentation({live:false,monitoring});assert.equal(shown.reason,'진동 변화');assert.equal(shown.feedback,'검토된 비정상과 유사');assert.equal(shown.modelId,'new-normal');
 const recording=continuousMonitorPresentation({live:true,monitoring:{...monitoring,recording:true}});assert.equal(recording.reason,shown.reason);
 assert.equal(continuousMonitorPresentation({monitoring:{...monitoring,fresh:false}}).feedback,'판단 대기');
 assert.equal(continuousMonitorPresentation({monitoring},true).stale,true);
 assert.equal(continuousMonitorPresentation({}),null);
});
test('monitor display does not turn stopped or incompatible input into normal classification',()=>{
 const m={fresh:true,model_id:'normal',feedback_model_id:'feedback',result:{state:'unknown',reason:'정지 상태 · 운전 판단 대기'}};
 assert.equal(continuousMonitorPresentation({monitoring:m}).feedback,'정지 상태 · 운전 판단 대기');
 assert.equal(continuousMonitorPresentation({monitoring:{...m,feedback_model_id:null}}).feedback,'분류 모델 미적용');
});
