# Arena Hero 鎴樻湳妗嗘灦 鈥斻€岃祫婧愪紭鍏?+ 鍧囪　闃插畧銆?

闈㈠悜 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 鐨?*鍙暱鏈熻繍琛?* Python 鎴樻湳瀹㈡埛绔€?
鍩轰簬瀹樻柟 SDK [`arena-hero`](https://pypi.org/project/arena-hero/)锛屼綘鎺屾帶娓告垙寰幆锛涙湰浠撳簱鍙仛鍐崇瓥銆?

# **绀惧尯锛歔linux.do](https://linux.do)**

> 绀惧尯鎴樻湳绀轰緥锛岄潪瀹樻柟瀹㈡埛绔€備粨搴撳唴**涓嶅寘鍚?*浠讳綍鐪熷疄 API Key銆?  
> 瑙勫垯鍩哄噯锛?*鐜╂硶 v0.14** 路 API v0.1 路 **SDK 鈮?0.2.9**锛?.2.8 浼?ProtocolError锛夈€?

## 娓告垙涓€鍙ヨ瘽

鍏变韩姘镐箙缃戞牸涓栫晫锛汚gent 姣?Tick 鐪嬬鏈夎閲庛€佷氦涓€浠?15 绉掔獥鍙ｅ唴鐨勮鍒掋€? 
**娌℃湁瀹樻柟閫氬叧**鈥斺€旀湁鏁堢洰鏍囨槸 Core 瀛樻椿銆佽祫婧愭寰幆銆佺紪鍒舵墿寮狅紱Champion Beacon 鏄彲閫夊弻鍊嶉噰闆嗕箻鏁般€?

璇︾粏鐞嗚В瑙侊細[`docs/GAME_UNDERSTANDING.md`](docs/GAME_UNDERSTANDING.md)
鎴樻湳鍘熷垯瑙侊細[`docs/STRATEGY.md`](docs/STRATEGY.md)

### v2 鍗囩骇閫熻

鏈 v2 鍥寸粫銆?*缁忔祹鏇村揩姝ｅ惊鐜?+ 鏇存棭婊＄紪 20 + 鎺㈢储涓嶇┖杞?+ 宸ヤ汉鏅鸿兘鍒嗗伐 + 鐪熷疄鏈?Beacon 鎺ㄨ繘 + 鏈湴 Dashboard 瀹炴椂瑙傛祴**銆嶅仛浜嗗杞崌绾э細

| 鍗囩骇椤?| 瑕佺偣 |
|--------|------|
| 馃殌 **鍙伴樁鍨?12W/4V/4R 鐖潯** | W 杈?3/6/9/12 鈫?鎻掓帓 V/R锛岃妭濂忔槑纭笉杩斿伐 |
| 馃 **鐭跨偣鏅鸿兘璋冨害** | Worker 婊¤浇鍙戠幇鐭挎椂锛屽惈闅滅瀵昏矾浼扮畻瀵规瘮銆岃嚜閲囧線杩斻€峷s銆屾淳鏈€杩戠┖闂层€嶏紝鎵ц鏇寸煭璺緞 |
| 馃寑 **鍙屼腑蹇冭灪鏃嬫帰绱?* | 鍐呯幆锛坉鈮?4锛塁ore 涓績铻烘棆 鈫?鐜垎鍒颁笂闄愯嚜鍔ㄥ垏鐩镐綅鈫?**Beacon 瀵煎悜澶栫幆鎺ㄨ繘**锛堜笉鍐嶅皬鑼冨洿姝诲惊鐜級|
| 馃П **闅滅鍘嗗彶涓诲姩閬块殰** | 鍚屾柟鍚?鈮? 娆¤鎸¤瘎鍒?-100锛?*閬垮紑"鑰佸牭澧?** 鑺傜渷绌鸿浆 tick |
| 馃攷 **P1-P3 瑙傛祴鎬?* | Core 杩佸緳璇勪及/瀹堢幆鍒嗘暎/闄堟棫鍥炶/鐏姏 ledger/SDK 鐗堟湰鑷/缁忔祹鍋ュ悍 stall 璇婃柇 |
| 馃憗 **瀹樻柟瑙嗛噹宸叉帰** | `explored_cells` 鎸夊崟浣?FOV 鍐欏叆锛欳ore 5 / Worker 3 / Vanguard 4 / Ranger 5锛堟浖鍝堥】锛夛紝闅滅閬尅 LOS |
| 馃椇 **鏈湴 Dashboard** | `--dashboard` 鍚?Flask 鍦板浘锛氬崟浣?璺緞/闅滅/宸叉帰/璧勬簮锛?00ms 杞 + `Cache-Control: no-store` 瀹炴椂鍒锋柊 |
| 馃洡 **杩?Core 浼樺厛 deposit / 璐村缁曡** | 婊¤揣杩戝浼樺厛涓婄即锛涗富杞村牭澧欐椂 `wall_follow_step` 璐村缁曡锛屽噺灏戝彛琚嬫尟鑽?|

## 鎴樻湳鐩爣

| 闃舵 | 琛屼负 |
|------|------|
| 鏃╂湡 | 浼樺厛鐢熶骇 Worker锛孷ISIBLE 閲囬泦 鈫?deposit锛屾墦閫氱粡娴?|
| 涓湡 | 鍚?**12 Worker + 鑻ュ共 Vanguard/Ranger** 婊＄紪锛堝缓璁€?pop鈮?0锛?|
| 鍏ㄧ▼ | Core 鍛ㄨ竟闃插畧锛涘▉鑳佹椂鎾ら€€/鍙嶅嚮浼樺厛浜庢墿寮?|
| Beacon | **鏈€澶?1 鍚?dedicated** 渚﹀療锛涜繙璺濇斁寮冿紱Core 涓嶅疁杩戒俊鏍?|
| 鐢熷瓨 | Core/鍗曚綅浣庤鏈夋潯浠?heal銆佷慨鐩撅紱v0.14 **鏃犵淮鎶よ垂** |
| 鐩戞帶鎸囨爣 | 鐢ㄦ埛鐩爣锛欳ore 搴撳瓨 **resources 鈮?100** |

## 椤圭洰缁撴瀯

```
arena-hero-tactic/
  README.md
  deploy.bat / deploy.sh # 涓€閿儴缃插叆鍙ｏ紙Windows 鍙屽嚮 / Unix锛?
  docs/
    GAME_UNDERSTANDING.md   # 娓告垙鎬庝箞杩愯 / 鐩爣 / Agent 鑱岃矗
    STRATEGY.md             # 鎴樻湳鍘熷垯涓庢敼閫犺矾绾?
    system_design*.md       # 鏋舵瀯涓庢帰绱㈣璁?
  bot/
    main.py              # 鍏ュ彛锛欿ey 鈫?杩炴帴 鈫?turns 寰幆锛堝彲閫?--dashboard锛?
    config.py            # TacticConfig
    strategy.py          # decide(turn)
    economy.py           # 閲囬泦/浜や粯/鐢熶骇/铻烘棆鎺㈢储
    combat.py            # 濞佽儊銆侀槻瀹堝湀銆乻weep/shoot
    pathing.py           # 闃叉姈姝ヨ繘銆佽灪鏃嬨€乥eacon 鐩爣銆佽创澧欑粫琛?
    memory.py            # 璧勬簮/闅滅/chunk/瀹樻柟 FOV 宸叉帰
    roles.py             # 瑙掕壊鍒嗛厤
    rules.py             # 鍔ㄦ€佸崟浣嶄环 / 瀹归噺 / chunk 閰嶉
    dashboard.py         # 鍙€夋湰鍦拌娴嬶細蹇収鐜紦鍐?+ Flask API + SSE 鏃ュ織
    dashboard_static/    # Dashboard 鍓嶇锛堝湴鍥?/ 瓒嬪娍 / 鏃ュ織锛?
  scripts/
    deploy.py            # 涓€閿細venv / 渚濊禆 / .env / 鍚?Dashboard / health
    restart_agent.py     # 鐜宸插氨缁椂浠呭悗鍙伴噸鍚?agent
  tests/
  deliverables/
```

## 鐜瑕佹眰

- Python **3.11+**
- `arena-hero>=0.2.9,<0.3`銆乣python-dotenv`锛堝彲閫夛級銆乣pytest`锛堟祴璇曪級
- **鍙€?Dashboard**锛歚flask>=3.0`锛坄requirements.txt` 涓粯璁ゆ敞閲婏紱浠?`--dashboard` 鏃堕渶瑕侊級
- **SDK 鐗堟湰鑷锛坴2 P3-1锛?*锛氬惎鍔ㄦ椂 `main.run_loop` 浼氬己鍒舵牎楠?arena-hero 鐗堟湰 鈮?0.2.9 涓?< 0.3锛涗笉婊¤冻鐩存帴 `SystemExit(1)`锛岄伩鍏?`ProtocolError` 鍒扮嚎涓婃墠鎶ラ敊

## 涓€閿儴缃诧紙鎺ㄨ崘锛?

鍏嬮殕浠撳簱鍚庯紝鐢ㄦ牴鐩綍鑴氭湰鑷姩瀹屾垚锛氭鏌?Python 鈮?3.11 鈫?鍒涘缓/澶嶇敤 `.venv` 鈫?瀹夎 `requirements.txt` + Flask锛圖ashboard锛? psutil 鈫?鍒濆鍖?`.env` 鈫掞紙鍙€夛級缁撴潫鏃?`bot.main` 鈫?鍚庡彴鍚姩 `python -m bot.main -v --dashboard` 鈫?鎺㈡祴 `GET /health`銆?

```bash
# Windows锛堜篃鍙祫婧愮鐞嗗櫒鍙屽嚮 deploy.bat锛?
deploy.bat
deploy.bat --api-key 浣犵殑_API_KEY
deploy.bat --no-start          # 鍙鐜涓嶅惎鍔?
deploy.bat --skip-pip          # 宸茶濂戒緷璧栨椂璺宠繃 pip
deploy.bat --no-kill           # 涓嶇粨鏉熷凡鍦ㄨ窇鐨?agent
deploy.bat --port 8765

# Linux / macOS
chmod +x deploy.sh
./deploy.sh
./deploy.sh --api-key 浣犵殑_API_KEY

# 绛変环鐩存帴璋冪敤
python scripts/deploy.py
python scripts/deploy.py --foreground   # 鍓嶅彴璺戯紝Ctrl+C 鍋?
python scripts/deploy.py --quiet        # 鍚姩涓嶅姞 -v
```

鎴愬姛鍚庢墦寮€ **http://127.0.0.1:8765/**锛汸ID 鍐欏湪 `logs/agent.pid`锛屾棩蹇楀湪 `logs/agent.log`銆?
**涓嶈**鎶?`.env` 鎴栫湡瀹?Key 鎻愪氦鍒?Git锛沗--api-key` 鍙啓鍏ユ湰鍦?`.env` 涓斾笉浼氬湪缁堢鎵撳嵃鏄庢枃銆?

浠呴噸鍚紙鐜宸插氨缁級锛歚python scripts/restart_agent.py`銆?

## 鎵嬪姩瀹夎

```bash
cd arena-hero-tactic
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
# 鑻ュ惎鐢?Dashboard锛?
# pip install "flask>=3.0"
```

## 閰嶇疆 API Key

```bash
copy .env.example .env          # Windows
# cp .env.example .env          # Unix
```

缂栬緫 `.env`锛?

```
ARENA_HERO_API_KEY=浣犵殑_API_KEY
```

**涓嶈**鎶?`.env` 鎴栫湡瀹?Key 鎻愪氦鍒?Git銆?

## 鍚姩

```bash
python -m bot.main
python -m bot.main -v --log-file logs/agent.log
python -m bot.main --max-turns 50

# 鏈湴瀹炴椂鍦板浘 / 鏃ュ織闈㈡澘锛堥粯璁?http://127.0.0.1:8765锛?
python -m bot.main --dashboard
python -m bot.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8765
```

姣忎釜 Tick 鏀跺埌 `state` 鍚庡敖蹇?`decide(turn)` 骞?`turn.submit()`銆傚懡浠ょ獥鍙ｇ害 15 绉掞紝鍐崇瓥椤昏交閲忋€?

### Dashboard锛堝彲閫夎娴嬶紝闆舵薄鏌撲富寰幆锛?

- **榛樿鍏抽棴**锛氫笉鍔?`--dashboard` 鏃朵笉瀵煎叆 Flask銆佷笉鍚?HTTP 绾跨▼锛屽喅绛栬矾寰勪笌绾夸笂涓€鑷淬€?
- **鍚悗鑳藉姏**锛?
  - 鍦板浘锛欳ore / Worker / Vanguard / Ranger / 鏁屼汉 / 璧勬簮 / 闅滅 / **瀹樻柟 FOV 宸叉帰鏍煎瓙** / 璺緞 dry-run 璺偣
  - 椤舵爮锛歵ick銆佽祫婧愩€佺紪鍒躲€乴ive/paused/stale 妯″紡銆佽窛涓婃鎴愬姛鎷夊彇鐨勫勾榫?
  - 鍘嗗彶瓒嬪娍锛歚/api/state/history` 杩斿洖 `{ok, frames, count}`锛涘墠绔?`normalizeHistory` 瑙ｅ寘
  - 鏃ュ織锛歋SE `/api/logs/stream` + 杞鍏滃簳
- **瀹炴椂鎬?*锛氬墠绔害 **500ms** 杞 `latest`+`history`锛岃姹傚甫 `cache: no-store` 涓?bust query锛涘悗绔椤甸潰涓庣姸鎬?API 鍐?`Cache-Control: no-store`銆?
- **鍋ュ悍妫€鏌?*锛歚GET /health` 鈫?`{ok, tick, ...}`銆?

## 鎴樻湳閫昏緫鎽樿

### 鐢熶骇浼樺厛绾э紙Core 绌洪棽涓旈潪鍗辨€ユ不鐤楋級

1. Worker < 鐩爣 鈫?`spawn WORKER`锛堝姩鎬佷环鏍硷級
2. 鍙濞佽儊涓旀垬鏂楀崟浣嶄笉瓒?鈫?`VANGUARD` / `RANGER`
3. 鍜屽钩鏈熻ˉ榻愮洰鏍囨垬鏂楀崟浣?
4. 璧勬簮涓嶈冻搴旀€ュ偍澶?鈫?璺宠繃 spawn
5. 浜哄彛瑙﹀強 `max_population` 鈫?鍋滄墿

### Worker

- 鏈?cargo 鈫?浼樺厛鍥?Core `deposit`锛?*杩?Core 鏃惰繘涓€姝ユ彁楂?deposit 浼樺厛绾?*锛屽噺灏戙€岄棬鍙ｅ緲寰娿€嶏級
- 绔欏湪鍙 `resource_cells` 鈫?`harvest`
- **鐭跨偣鏅鸿兘璋冨害锛坴2锛?*锛?
  - 绌鸿浇鍙戠幇鐭?鈫?鐩存帴閲囬泦锛堜笌 v1 鐩稿悓锛?
  - **婊¤浇鍙戠幇鐭?* 鈫?鐢?`estimate_path_steps`锛?*鍚殰纰嶅璺及绠?*锛夌簿纭姣旓細
    - 閫夐」 A銆岃嚜閲囧線杩斻€? 閫佸洖 Core 鈫?鍐嶅洖鐭?鐨勬€绘鏁?
    - 閫夐」 B銆屾淳鏈€杩戠┖闂?Worker銆? 鍏朵粬 idle Worker 鍒扮熆 鈫?鍥?Core 鐨勬渶鐭鏁?
    - 閫夋洿鐭殑锛欰 鑳?鈫?鍐欏叆棰勭害锛堥€佸畬 cargo 涓嬩竴 tick 浼樺厛杩旂▼閲囷級锛汢 鑳?鈫?绔嬪埢鎸囨淳鏈€浼?idle Worker
    - 棰勭害 TTL 16 tick锛汻ETREAT/HEAL 瑙掕壊绔嬪埢閲婃斁
- **鍙屼腑蹇冭灪鏃嬫帰绱紙v2锛屼慨澶嶄笉鍐嶅皬鑼冨洿寰幆锛?*锛?
  - 鍐呯幆锛氳窛 Core 鈮?24 鏍?鈫?Core 涓績铻烘棆锛屾墖鍖哄垎鏁?+ 璺宠繃宸叉帰 chunk
  - 鐜帹杩涘埌涓婇檺鍚?**鑷姩鍒?Beacon 鐩镐綅** 鈫?澶栫幆浠?Beacon 涓轰腑蹇冭灪鏃嬶紝涓嶅啀鍥為€€鍒板唴鐜紙v2 鍏抽敭 bugfix锛?
  - 璺濈 Beacon 澶繙锛?64锛夋垨宸ヤ汉涓嶈冻锛?3锛夆啋 瀹堥棬涓嶈拷锛岄槻姝㈤タ姝荤粡娴?
- **鍘嗗彶闅滅涓诲姩閬块殰锛坴2锛?*锛氬璺椂鑻ユ煇閭绘牸鍘嗗彶琚尅 鈮? 娆?鈫?璇勫垎 -100 涓诲姩缁曞紑锛?-2 娆?-30 闄嶆潈
- **璐村缁曡**锛氫富杞磋纭鎸′綇鏃?`wall_follow_step` 閫変晶鍚戣创澧欙紝鍑忚交鍙ｈ鏉ュ洖鎶?
- 闄勮繎鏁屼汉 鈫?鍚?Core 鎾ら€€锛涗綆琛€ 鈫?鍥炲煄 heal

### Vanguard / Ranger

- 閭绘牸 / 灏勭▼鍐呮敾鍑伙紱鍚﹀垯椹诲畧 Core 闃插畧鐜?
- **瀹堢幆浣嶅垎鏁ｏ紙v2 P1-2锛?*锛氬鍗曚綅鍚岀浉浣?slot 鍐茬獊鏃讹紝+1 鍋忕Щ閬胯锛屼笉鍐嶅爢鍚屾牸鍫?Core 鍏ュ彛
- Ranger 灏勫嚮锛?*鐏姏 ledger 杞婚噺锛坴2 P2-2锛?*锛岄璁′激瀹冲凡婊?HP 鐨勭洰鏍囪烦杩囷紝閬垮厤 overkill锛堟棩蹇?`shoot_avoid_overkill`锛?

### Core

- HP/鐩捐繃浣?鈫?`heal` / `repair_shield`锛堟垬鏂楀悗缁撶畻锛屽彲棰勬帓锛?
- 鍚﹀垯鎸?*鍙伴樁鍨嬬敓浜ц妭濂忥紙v2锛?* `spawn`锛歐=3鈫扸锛沇=6鈫扸+R锛沇=9鈫扸+R锛沇=12鈫扸+R+R锛涜揪鍒?12/4/4 鍋滄墿
- **Core 杩佸緳璇勪及锛坴2 P1-1 鏃ュ織-only锛?*锛氶偦鏍兼晫涓?Core HP鈮? / Beacon 璺?Core鈮? 鏃讹紝鍐欏叆璇勪及鏃ュ織浣?*涓嶇湡姝?start_move**锛堥伩鍏嶇Щ鍔?bug锛?
- 榛樿涓嶄富鍔ㄨ拷 Beacon锛涘悗缁彲瀹炵幇銆孋ore 杩滅淇℃爣銆嶈縼寰?

### 鍦板浘璁板繂涓庡畼鏂硅閲?

- `MemoryMap`锛氳祫婧愮姸鎬佹満銆佹案涔呴殰纰嶃€佹帀钀?cargo銆?6脳16 chunk 宸叉帰涓庨檲鏃у洖璁?
- **榛樿钀界洏**鍒?`.arena_hero_state.json`锛堝凡 gitignore锛夛細鍚姩鍔犺浇銆佹瘡 N tick 涓庤繘绋嬮€€鍑烘椂淇濆瓨锛?*閲嶅惎 agent 涓嶄涪宸叉帰**
- **宸叉帰鏍煎瓙 = 瀹樻柟 FOV 鍘嗗彶**锛堥潪銆岃蛋杩囩殑瓒宠抗銆嶏級锛?
  - 鍗婂緞锛堟浖鍝堥】锛夛細**Core 5 / Worker 3 / Vanguard 4 / Ranger 5**
  - 闅滅閬尅瑙嗙嚎锛坄has_line_of_sight`锛夊悗鍐欏叆 `explored_cells`
  - Dashboard 瀵煎嚭鏍煎瓙绾у凡鎺紝渚夸簬瀵圭収鐪熷疄鍙鑼冨洿
- **Beacon 鍧愭爣**锛氭瘡 tick 鐢?`decide()` 浠?`turn.beacon` 鍚屾鍒?`config.beacon_position`锛圙ROUND/None 鍐欎綅缃紝CARRIED 娓呯┖锛夛紱鎺ㄨ繘鐢?`beacon_progress_target` 閫愭闈犺繎

## 榛樿鍙傛暟锛坄bot/config.py`锛屼互浠ｇ爜涓哄噯锛?

| 鍙傛暟 | 鍏稿瀷榛樿 | 璇存槑 |
|------|----------|------|
| `max_population` | **20** | 鍩虹浠锋弧缂栫‖椤?|
| `target_workers` | **12** | 鐩爣宸ヤ汉 |
| `target_vanguards` | **4** | 鐩爣鍏堥攱 |
| `target_rangers` | **4** | 鐩爣娓镐緺 |
| `spiral_base_ring` | 3 | 鏈湴铻烘棆璧峰鐜?|
| `spiral_max_ring` | **24** | 鏈湴铻烘棆涓婇檺锛堟敹绱х┖杞級 |
| `sector_count` | **4** | Worker 鎵囧尯鍒嗘暎 |
| `beacon_max_chase` | **10000** | Core鈫払eacon 瓒呰窛涓嶈拷锛堥粯璁も増涓嶉檺锛岀嚎涓?d鈮?000 鍙拷锛?|
| `beacon_min_workers` | **3** | 鏃╂湡鍏ㄥ憳閲囷紝澶熶汉鍐?1 浜轰睛瀵?|
| `beacon_push_population` | **10** | 鎬讳汉鍙?鈮?姝ゅ€?鈫?鍚戜俊鏍囨帹杩?|
| `beacon_push_explore_ratio` | **0.8** | 鏈湴锛坰piral_max_ring锛夋帰绱㈠害 鈮?姝ゆ瘮渚?鈫?鍚戜俊鏍囨帹杩?|
| `recall_stall_ticks` | 6 | 鏃犺繘灞曡蒋鍥炴挙 |
| `retreat_adjacent` | 1 | 绌鸿揣璐磋韩鎵嶆挙 |
| `retreat_radius` | 3 | 婊¤揣淇濇姢鍗婂緞 |
| `beacon_step_radius` | 8 | Beacon 闃舵姝ヨ窛 |
| `CHUNK_SIZE` | **16** | 鍦板浘鍧楀昂瀵革紙MemoryMap explored 鏍囪绮掑害锛泇2 浠?32 鈫?16锛屽垏 chunk 鏇撮瀵嗭級|
| `refresh_interval_ticks` | 4 | 璧勬簮鍥炶ˉ鑺傛媿 / 闄堟棫 chunk 鍥炶鍩哄噯锛堥檲鏃ч槇鍊?= refresh_interval * 50 鈮?200 tick锛墊

璋冨弬锛氭敼 `TacticConfig` 鎴?`decide(turn, config=...)`銆?

**寤鸿锛?* 鎬荤紪鍒朵繚鎸?**鈮?0**锛岀敤婊?v0.14 鍩虹浠锋牸鍖洪棿锛涚 21 涓崟浣嶅紑濮嬪姩鎬佹定浠枫€?

> **鐢熶骇鑺傚锛坴2锛?*锛歐orker 杈?3/6/9/12 鍙伴樁鏃舵寜搴忔彃鎺?VANGUARD 涓?RANGER锛屾渶缁堢洰鏍?12W / 4V / 4R锛堝熀纭€浠锋弧缂?20锛夈€?

## 瀹樻柟瑙勫垯閫熸煡锛坴0.14锛?

- 鍛戒护绐楀彛 **15s**锛涙瘡瀵硅薄姣?tick **涓€涓?*鍔ㄤ綔
- **鏃?*姣?tick 缁存姢璐癸紱鍔ㄦ€佷环锛歚k=max(0,floor((pop-20)/5)+1)`锛宍price鈮坆ase脳1.3^k`
- 鍩虹浠凤紙pop 0鈥?9锛夛細Worker **5** / Vanguard **10** / Ranger **12**
- 璧勬簮锛氭瘡 4 resolved tick 鎸?chunk 閰嶉琛ョ偣锛涙寔 Beacon 鏃?harvest 脳2
- 缁撶畻椤哄簭瑕佺偣锛氱Щ鍔?鈫?harvest/deposit 鈫?鎴樻枟 鈫?heal 鈫?spawn
- Beacon锛氬潗鏍囧叕寮€锛汼DK `status=None` 瑙嗕负鍦伴潰鍙拷韪?
- **瑙嗛噹锛堟浖鍝堥】锛?*锛欳ore **5** / Worker **3** / Vanguard **4** / Ranger **5**锛涢殰纰嶅彲鎸¤绾?

鏇村锛歔娓告垙瑙勫垯](https://doc.arenahero.io/zh-Hans/rules/world-and-ticks) 路 [瑙勫垯閫熸煡](https://doc.arenahero.io/zh-Hans/reference/numbers) 路 [Python SDK](https://doc.arenahero.io/zh-Hans/sdk/quickstart)

## 绂荤嚎娴嬭瘯

```bash
# 寤鸿鍦?venv 涓?
pytest -q
pytest tests/test_pathing.py tests/test_economy.py tests/test_memory.py -v
pytest tests/test_dashboard.py -q   # 闇€ flask锛汚PI 鍖呰/鐜紦鍐?闆舵薄鏌?绛夋満鍒舵祴
```

```python
from bot.strategy import decide
result = decide(turn)   # 鍙帓闃燂紝涓?submit
print(result.summary())
```

**瑙傛祴鎬ч獙璇侊紙v2 涓撶敤锛?*锛氱敤浠撳簱鍐?stub 浠跨湡鎴栫嚎涓?`--dashboard` 瀵圭収锛?

- `core:spawn:WORKER / VANGUARD / RANGER` 鈫?鍙伴樁鑺傚姝ｇ‘
- `dispatch:option=self / option=other` 鈫?鐭跨偣璋冨害鐢熸晥
- `:ring=`锛堝唴鐜級涓?`phase=beacon`锛堝鐜級鍚屾椂鍑虹幇 鈫?鍙屼腑蹇冨垏鎹㈡甯?
- `new_chunk=` 鈫?鎺㈢储鍦ㄦ帹杩涳紱`pickup_beacon=` 鈫?宸插疄闄呭埌 Beacon
- Dashboard 鍦板浘 tick 闅忔椂闂撮€掑銆佸凡鎺㈡牸闅忓崟浣嶇Щ鍔ㄦ墿寮?
- `ERROR` / `ProtocolError` = 0

## 璁捐璇存槑

- **I/O 鍒嗙**锛歚strategy.decide` 鍙函娴嬶紱`main` 鍙繛鎺ヤ笌 submit
- **鍦板浘璁板繂**锛歚MemoryMap` 鍙法閲嶅惎钀界洏锛坄.arena_hero_state.json`锛夛紱鏈嶅姟绔笉鍥炴斁鍘嗗彶 FOV锛屾晠 Agent 蹇呴』鑷瓨锛涘惈璧勬簮/闅滅/chunk 涓夌淮 + chunk_last_seen 闄堟棫鍒ゅ畾锛?00 tick锛? **瀹樻柟 FOV 宸叉帰**
- **闃叉姈瀵昏矾**锛歚clamp_step_toward_memo` 閬垮厤闅滅瀵规姈锛泇2 **鍙犲姞鍘嗗彶闅滅闄嶆潈**锛堚墺3 娆¤鎸?-100锛夛紱纭涓昏酱鍫垫椂璐村缁曡
- **璺緞浼扮畻锛坴2锛?*锛歚estimate_path_steps` / `reconstruct_path` dry-run锛屽彧鐢ㄤ簬鐭跨偣璋冨害涓?Dashboard 鍙鍖栵紝涓嶆敼鍙樼湡瀹炵姸鎬?
- **鍙伴樁鐢熶骇锛坴2锛?*锛歚choose_spawn` 鎸?3/6/9/12 鍥涙。鍙伴樁鎻掓帓 V/R锛岀粓鎬?12/4/4 鍩虹浠锋弧缂?20
- **鍙屼腑蹇冭灪鏃嬶紙v2锛?*锛歚dual_spiral_target` 鍐呯幆 Core + 澶栫幆 Beacon锛岀幆鐖嗗悗鑷姩鍒囩浉浣嶏紝**涓嶅啀鍥為€€鍒板唴鐜皬鑼冨洿姝诲惊鐜?*
- **缁忔祹鍋ュ悍锛坴2 P3-2锛?*锛氳繛缁?50 tick 鏃?deposit 鈫?鎵撶粨鏋勫寲棰勮鏃ュ織锛屽洖 40 闃插埛灞忥紱256 tick GC 姝讳骸 Worker 鐩稿叧妯″潡瀛楀吀 4 涓?
- **Dashboard 闆舵薄鏌?*锛氫粎 flag 寮€鍚椂鍚庡彴绾跨▼ + 鐜舰缂撳啿锛沗safe_push_snapshot` 寮傚父鍚炴帀锛屼笉闃绘柇 `submit`
- **澶辫触瀹夊叏**锛氬崟 turn 寮傚父璁版棩蹇楀苟灏介噺涓嶅崱姝诲惊鐜?

## License

MIT
