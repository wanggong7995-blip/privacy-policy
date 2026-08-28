/* saju.html 안의 계산 코어를 그대로 꺼내 검증한다.
 * 실행: node saju.test.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'saju.html'), 'utf8');
const m = html.match(/<script id="saju-core">([\s\S]*?)<\/script>/);
if (!m) { console.error('saju-core 스크립트를 찾지 못했습니다.'); process.exit(1); }
const S = eval(m[1] + ';SajuCore');

let pass = 0, fail = 0;
function eq(actual, expected, label) {
  if (String(actual) === String(expected)) { pass++; }
  else { fail++; console.log('  ✗ ' + label + '  기대: ' + expected + '  실제: ' + actual); }
}
function group(name, fn) { console.log('\n· ' + name); fn(); }

const gj = p => S.gjName(p);
function chart(o) {
  return S.buildChart(Object.assign({
    gender: 'M', longitude: 126.98, tz: null,
    trueSolar: false, eot: false, nightZi: false,
    hour: 12, minute: 0, hasTime: true
  }, o));
}

group('일주 (60갑자 연속성)', () => {
  const day = (y, m, d) => {
    const i = S.dayGanjiIdx(y, m, d);
    return S.GAN[i % 10] + S.JI[i % 12];
  };
  eq(day(1900, 1, 1), '갑술', '1900-01-01');
  eq(day(1949, 10, 1), '갑자', '1949-10-01');
  eq(day(1970, 1, 1), '신사', '1970-01-01 (유닉스 에포크)');
  eq(day(2000, 1, 1), '무오', '2000-01-01');
  // 60일 주기 일관성
  eq(day(2000, 1, 1), day(2000, 3, 1), '60일 뒤 같은 일진');
});

group('사주 원국 전체 (알려진 사례)', () => {
  // 1949년 10월 1일 19:30 → 己丑年 癸酉月 甲子日 甲戌時
  const c = chart({ y: 1949, m: 10, d: 1, hour: 19, minute: 30 });
  eq(gj(c.pillars.year), '기축', '연주');
  eq(gj(c.pillars.month), '계유', '월주');
  eq(gj(c.pillars.day), '갑자', '일주');
  eq(gj(c.pillars.hour), '갑술', '시주');
});

group('연주 – 입춘 경계', () => {
  // 2024년 입춘: 2월 4일 17시 27분 무렵 (KST)
  eq(gj(chart({ y: 2024, m: 2, d: 4, hour: 10 }).pillars.year), '계묘', '입춘 전날 오전');
  eq(gj(chart({ y: 2024, m: 2, d: 5, hour: 10 }).pillars.year), '갑진', '입춘 다음날');
  eq(gj(chart({ y: 2024, m: 1, d: 20, hour: 10 }).pillars.year), '계묘', '1월생은 앞 해');
  eq(gj(chart({ y: 2024, m: 12, d: 31, hour: 10 }).pillars.year), '갑진', '12월생은 그 해');
  eq(gj(S.yearGanji(2026)), '병오', '2026년 간지');
});

group('월주 – 절입 기준', () => {
  eq(gj(chart({ y: 2026, m: 8, d: 28 }).pillars.month), '병신', '2026-08-28 처서 무렵');
  eq(chart({ y: 2026, m: 8, d: 28 }).monthTerm, '입추', '절기 이름');
  // 월지는 절기로만 정해진다: 6월 초는 망종(오월), 6월 말은 하지 지나도 여전히 오월
  eq(S.JI[chart({ y: 2025, m: 6, d: 10 }).pillars.month.j], '오', '2025-06-10 오월');
  eq(S.JI[chart({ y: 2025, m: 7, d: 10 }).pillars.month.j], '미', '2025-07-10 미월');
});

group('시주 – 시지와 진태양시', () => {
  const c1 = chart({ y: 2000, m: 5, d: 5, hour: 0, minute: 30 });
  eq(S.JI[c1.pillars.hour.j], '자', '00:30 자시');
  const c2 = chart({ y: 2000, m: 5, d: 5, hour: 23, minute: 30 });
  eq(S.JI[c2.pillars.hour.j], '자', '23:30 자시');
  eq(S.dayGanjiIdx(2000, 5, 5) % 60, c2.pillars.day.i, '기본값은 자정에 일진이 바뀐다');
  const c3 = S.buildChart({ y: 2000, m: 5, d: 5, hour: 23, minute: 30, hasTime: true,
    gender: 'M', longitude: 126.98, tz: null, trueSolar: false, eot: false, nightZi: true });
  eq(c3.pillars.day.i, (S.dayGanjiIdx(2000, 5, 5) + 1) % 60, '조자시 옵션이면 다음 날 일진');
  // 서울(126.98도)은 표준시보다 약 32분 늦다
  const c4 = chart({ y: 2000, m: 5, d: 5, hour: 12, trueSolar: true });
  eq(Math.round(c4.lonCorrMin), -32, '경도 보정 −32분');
});

group('서머타임 · 표준자오선', () => {
  eq(S.isDST(1988, 8, 15), true, '1988-08-15 서머타임');
  eq(S.isDST(1989, 8, 15), false, '1989-08-15 서머타임 아님');
  eq(S.isDST(1987, 5, 9), false, '1987-05-09 시행 전날');
  eq(S.utcOffsetHours(1958, 6, 1), 8.5, '1958년 동경 127.5도');
  eq(S.utcOffsetHours(1990, 6, 1), 9, '1990년 동경 135도');
  const c = chart({ y: 1988, m: 8, d: 15, hour: 12, minute: 0 });
  eq(c.dst, true, '서머타임 반영');
  eq(S.JI[c.pillars.hour.j], '오', '시계 12시 = 표준시 11시 → 오시');
});

group('음력 → 양력 (설날)', () => {
  const f = (y) => { const r = S.lunarToSolar(y, 1, false, 1);
    return r.y + '-' + String(r.m).padStart(2,'0') + '-' + String(r.d).padStart(2,'0'); };
  eq(f(2020), '2020-01-25', '2020 설날');
  eq(f(2021), '2021-02-12', '2021 설날');
  eq(f(2022), '2022-02-01', '2022 설날');
  eq(f(2023), '2023-01-22', '2023 설날');
  eq(f(2024), '2024-02-10', '2024 설날');
  eq(f(2025), '2025-01-29', '2025 설날');
  eq(f(2026), '2026-02-17', '2026 설날');
  eq(f(1990), '1990-01-27', '1990 설날');
});

group('음력 → 양력 (추석)', () => {
  const f = (y) => { const r = S.lunarToSolar(y, 8, false, 15);
    return r.y + '-' + String(r.m).padStart(2,'0') + '-' + String(r.d).padStart(2,'0'); };
  eq(f(2022), '2022-09-10', '2022 추석');
  eq(f(2023), '2023-09-29', '2023 추석');
  eq(f(2024), '2024-09-17', '2024 추석');
  eq(f(2025), '2025-10-06', '2025 추석');
  eq(f(2026), '2026-09-25', '2026 추석');
});

group('양력 → 음력 (연도 표기)', () => {
  const f = (y, m, d) => { const l = S.solarToLunar(y, m, d);
    return l.year + '-' + (l.leap ? '윤' : '') + l.month + '-' + l.day; };
  eq(f(2024, 2, 9), '2023-12-30', '설 하루 전은 앞 해 섣달');
  eq(f(2024, 2, 10), '2024-1-1', '설날은 새 음력 해의 정월 초하루');
  eq(f(1975, 11, 3), '1975-10-1', '11월에 시작하는 음력 10월');
  eq(f(2023, 12, 31), '2023-11-19', '양력 연말은 음력 11월');
  eq(f(2025, 1, 5), '2024-12-6', '양력 1월 초는 앞 해 음력 12월');
  eq(f(2023, 4, 5), '2023-윤2-15', '윤달 표기');
  eq(f(2026, 8, 28), '2026-7-16', '오늘');
});

group('윤달', () => {
  eq(S.leapMonthOf(2012), 3, '2012 윤3월');
  eq(S.leapMonthOf(2014), 9, '2014 윤9월');
  eq(S.leapMonthOf(2017), 5, '2017 윤5월');
  eq(S.leapMonthOf(2020), 4, '2020 윤4월');
  eq(S.leapMonthOf(2023), 2, '2023 윤2월');
  eq(S.leapMonthOf(2025), 6, '2025 윤6월');
  eq(S.leapMonthOf(2024), 0, '2024 윤달 없음');
});

group('양력 ↔ 음력 왕복', () => {
  let bad = 0, n = 0;
  for (let y = 1901; y <= 2099; y += 7) {
    for (const [mm, dd] of [[1,5],[3,17],[6,30],[9,9],[12,25]]) {
      const l = S.solarToLunar(y, mm, dd);
      n++;
      if (!l) { bad++; continue; }
      const b = S.lunarToSolar(l.year, l.month, l.leap, l.day);
      if (!b || b.y !== y || b.m !== mm || b.d !== dd) {
        bad++;
        if (bad < 4) console.log('    왕복 실패', y, mm, dd, '→', JSON.stringify(l), '→', JSON.stringify(b));
      }
    }
  }
  eq(bad, 0, n + '건 왕복 변환');
});

group('대운', () => {
  // 양(陽)년생 남자 = 순행, 음(陰)년생 남자 = 역행
  const a = chart({ y: 2024, m: 5, d: 5, gender: 'M' });   // 갑진년(양) 남자
  eq(a.forward, true, '양년생 남자 순행');
  const b = chart({ y: 2024, m: 5, d: 5, gender: 'F' });
  eq(b.forward, false, '양년생 여자 역행');
  const c = chart({ y: 2025, m: 5, d: 5, gender: 'M' });   // 을사년(음) 남자
  eq(c.forward, false, '음년생 남자 역행');
  // 순행 대운은 월주 다음 간지부터
  eq(gj(a.daewoon[0].p), S.gjName(S.ganji(a.pillars.month.i + 1)), '첫 대운 간지');
  eq(a.daewoon.length, 10, '대운 10개');
  eq(a.daewoon[1].age - a.daewoon[0].age, 10, '10년 간격');
});

group('분석 결과 무결성', () => {
  let bad = 0, n = 0;
  for (let y = 1900; y <= 2100; y += 3) {
    const c = chart({ y, m: ((y * 7) % 12) + 1, d: ((y * 13) % 28) + 1, hour: y % 24 });
    n++;
    const okEl = S.ELEMS.every(e => isFinite(c.power[e]) && c.power[e] >= 0);
    const sum = S.ELEMS.reduce((a, e) => a + c.count[e], 0);
    if (!okEl || sum !== 8 || !c.strength || !c.need.length || c.daewoon.length !== 10) {
      bad++;
      if (bad < 4) console.log('    이상', y, sum, c.strength, c.need);
    }
  }
  eq(bad, 0, n + '개 연도 전수 계산');
});

group('절기 시각 (한국천문연구원 대조)', () => {
  const kst = (jd) => { const f = S.fromJD(jd + 9 / 24);
    return f.y + '-' + String(f.m).padStart(2,'0') + '-' + String(f.d).padStart(2,'0'); };
  eq(kst(S.termJD(2024, 0)), '2024-02-04', '2024 입춘');
  eq(kst(S.termJD(2025, 0)), '2025-02-03', '2025 입춘');
  eq(kst(S.termJD(2026, 0)), '2026-02-04', '2026 입춘');
  eq(kst(S.termJD(2024, 21)), '2024-12-21', '2024 동지');
  eq(kst(S.termJD(2025, 9)), '2025-06-21', '2025 하지');
});

console.log('\n' + (fail === 0 ? '✅' : '❌') + '  통과 ' + pass + ' / 실패 ' + fail + '\n');
process.exit(fail ? 1 : 0);
