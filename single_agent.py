import os
import json
import time
from typing import Iterable, Callable, Any, Dict
import httpx
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FunctionTool, MessageRole


def ensure_env() -> tuple[str, str]:
    load_dotenv()
    endpoint = os.environ.get("PROJECT_ENDPOINT")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME")
    if not endpoint or not model:
        raise RuntimeError("PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in environment (.env)")
    return endpoint, model


def build_weather_tool() -> tuple[FunctionTool, Dict[str, Callable[..., Any]]]:
    """FunctionTool 정의: (1) 도시/국가 지오코딩, (2) 미국 NWS 예보 요약.

    사용 흐름:
      1) 사용자가 "국가, 도시"를 제공하면 geocode_city_country 먼저 호출 → lat/lon 획득
      2) 국가 코드가 US인 경우 get_weather_summary 호출로 예보 요약
      3) US 이외 국가는 현재 지원 안함 메시지 반환
    """
    base = "https://api.weather.gov"
    user_agent = os.environ.get("NWS_USER_AGENT") or "WeatherAgentDemo (contact: you@example.com)"
    # NWS 권장 Accept: application/geo+json (일부 예에서는 ld+json). 둘 다 시도.
    base_headers = {"User-Agent": user_agent}

    # 간단한 한국어 → 국가코드 매핑 (필요시 확장 가능)
    country_alias = {
        "미국": "US",
        "미 합중국": "US",
        "usa": "US",
        "us": "US",
        "United States": "US",
    }

    def geocode_city_country(country: str, city: str) -> str:
        """오픈 지오코딩(Open-Meteo) API로 도시/국가 → 위도/경도.

        반환 JSON:
          { "city": str, "country_code": str, "lat": float, "lon": float }
          또는 { "error": str }
        미국(US) 이외 국가는 현재 NWS 예보 미지원 안내.
        """
        try:
            from urllib.parse import quote

            country = country.strip()
            city_original = city.strip()
            city_lower = city_original.lower()
            want_dc = "dc" in city_lower.replace(" ", "")  # '워싱턴 DC', 'Washington DC' 등 구분
            code = country_alias.get(country, country).upper()

            # 한국어 → 영어 도시명 매핑 및 변형 후보
            city_norm_map = {
                "워싱턴 dc": ["Washington", "Washington D.C.", "Washington DC"],
                "워싱턴": ["Washington", "Washington D.C.", "Washington DC"],
                "뉴욕": ["New York"],
                "로스앤젤레스": ["Los Angeles", "LA"],
                "샌프란시스코": ["San Francisco"],
                "시카고": ["Chicago"],
                "시애틀": ["Seattle"],
                "보스턴": ["Boston"],
                "휴스턴": ["Houston"],
                "댈러스": ["Dallas"],
            }

            base_key = city_original.lower().replace(".", "").strip()
            candidates = city_norm_map.get(base_key, [])
            # 기본 원문, 공백 제거, DC 제거 변형 추가
            variants = {city_original, base_key, base_key.replace(" dc", ""), base_key.replace("d c", "")}
            for v in list(variants):
                # 단순 대문자화 / capitalize 변형
                if len(v) > 1:
                    variants.add(v.title())
            for c in list(candidates):
                variants.add(c)
            # variants 정리
            candidate_list = [v for v in variants if v]
            # 영문 이외 문자열을 가진 항목은 그대로 두되 우선적으로 영어 후보 먼저 시도
            # Open-Meteo API language 파라미터 ko 한번, en 한번 시도

            tried = []
            chosen = None
            last_err = None
            collected_matches = []  # 후보 저장 후 가중치 평가
            with httpx.Client(timeout=15) as hc:
                for name_variant in candidate_list:
                    for lang in ("en", "ko"):
                        q = quote(name_variant)
                        url = f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=10&language={lang}&format=json"
                        r = hc.get(url)
                        tried.append({"name": name_variant, "lang": lang, "code": r.status_code})
                        if r.status_code >= 400:
                            last_err = f"geocoding failed {r.status_code} variant={name_variant} lang={lang}"
                            continue
                        data = r.json()
                        results = data.get("results") or []
                        if not results:
                            last_err = f"no result variant={name_variant} lang={lang}"
                            continue
                        for item in results:
                            if item.get("country_code", "").upper() != code:
                                continue
                            collected_matches.append(item)
                    # variant 별로 모두 수집 (즉시 break 하지 않고 누적)

            if collected_matches:
                # 가중치 산정
                ranked = []
                for it in collected_matches:
                    admin1 = (it.get("admin1") or "").lower()
                    name_val = (it.get("name") or "").lower()
                    pop = it.get("population") or 0
                    score = 0
                    # DC 특화 우선순위
                    if want_dc:
                        if "district of columbia" in admin1 or "washington, d.c" in name_val or name_val.endswith(", dc"):
                            score += 10000
                        # lat/lon 근사 (DC: ~38.8~-77.1)
                        latv = it.get("latitude") or 0
                        lonv = it.get("longitude") or 0
                        if 38.0 <= latv <= 39.5 and -78.0 <= lonv <= -76.0:
                            score += 5000
                    # 인구 많은 도시 선호
                    score += min(pop, 5_000_000) / 100  # 5e6 => +50000 max
                    ranked.append((score, pop, it))
                ranked.sort(key=lambda x: (-x[0], -x[1]))
                chosen = ranked[0][2]

            if not chosen:
                return json.dumps({
                    "error": "no geocoding result",
                    "attempts": tried,
                    "last_error": last_err,
                    "variants_considered": candidate_list,
                }, ensure_ascii=False)

            out = {
                "city": chosen.get("name"),
                "country_code": chosen.get("country_code"),
                "lat": chosen.get("latitude"),
                "lon": chosen.get("longitude"),
                "admin1": chosen.get("admin1"),
                "population": chosen.get("population"),
                "attempts": tried,
            }
            if out["country_code"].upper() != "US":
                out["warning"] = "현재 미국(US) 지역만 기상 예보 제공을 지원합니다. 위도/경도만 참고하세요."
            return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def get_weather_summary(lat: float, lon: float, hourly: bool = False, max_periods: int = 5) -> str:
        """위도/경도 좌표에 대한 날씨/예보 요약(JSON 문자열 반환).

        Args:
            lat: 위도
            lon: 경도
            hourly: True 시 시간별, False 시 일반(period) 예보
            max_periods: 출력할 최대 기간(기본 5)
        """
        try:
            def summarize_periods(periods, location_name, forecast_url, mode):
                simplified = []
                for p in periods:
                    start_iso = p.get("startTime")
                    end_iso = p.get("endTime")
                    date_only = None
                    if isinstance(start_iso, str) and "T" in start_iso:
                        date_only = start_iso.split("T", 1)[0]
                    simplified.append({
                        "name": p.get("name"),
                        "temp": f"{p.get('temperature')} {p.get('temperatureUnit')}",
                        "wind": p.get("windSpeed"),
                        "short": p.get("shortForecast"),
                        "start": start_iso,
                        "end": end_iso,
                        "date": date_only,
                    })
                bullets = []
                for item in simplified:
                    date_prefix = f"{item['date']} " if item.get('date') else ""
                    bullets.append(f"- {date_prefix}{item['name']}: {item['short']}, 온도 {item['temp']}, 바람 {item['wind']}")
                summary_ko = "\n".join(bullets)
                return {
                    "location": location_name,
                    "forecast_type": mode,
                    "forecast": simplified,
                    "summary_ko": summary_ko,
                    "source": forecast_url,
                }

            # 1) NWS points 조회 시도
            accept_candidates = ["application/geo+json", "application/ld+json", "application/json"]
            attempts = []
            last_error = None
            pjson = {}
            forecast_url = None
            location_name = "(unknown)"
            raw_point_text = None
            for accept in accept_candidates:
                try:
                    with httpx.Client(timeout=20, headers={**base_headers, "Accept": accept}) as hc:
                        pt = hc.get(f"{base}/points/{lat},{lon}")
                        body_trunc = pt.text[:280]
                        attempts.append({"accept": accept, "status": pt.status_code, "body_sample": body_trunc})
                        if pt.status_code >= 400:
                            last_error = f"points lookup failed {pt.status_code} (Accept={accept})"
                            continue
                        raw_point_text = pt.text
                        j = pt.json()
                        if not isinstance(j, dict):
                            last_error = f"unexpected points json type {type(j)}"
                            continue
                        pjson = j.get("properties", {}) or {}
                        forecast_url = pjson.get("forecastHourly" if hourly else "forecast")
                        rel = pjson.get("relativeLocation", {}).get("properties", {})
                        location_name = f"{rel.get('city','?')}, {rel.get('state','?')}" if rel else "(unknown)"
                        if forecast_url:
                            break
                        last_error = f"forecast url missing (Accept={accept})"
                except Exception as ie:
                    last_error = f"points request error {ie} (Accept={accept})"
                    attempts.append({"accept": accept, "status": "exception", "error": str(ie)})

            # 2) gridpoints fallback
            if not forecast_url and pjson:
                cwa = pjson.get("cwa")
                gridX = pjson.get("gridX")
                gridY = pjson.get("gridY")
                if cwa and gridX is not None and gridY is not None:
                    forecast_url = f"{base}/gridpoints/{cwa}/{gridX},{gridY}/{'forecast/hourly' if hourly else 'forecast'}"
                    last_error = f"constructed forecast url via gridpoints fallback: {forecast_url}"

            # 3) NWS Forecast fetch
            if forecast_url:
                try:
                    with httpx.Client(timeout=20, headers={**base_headers, "Accept": "application/geo+json"}) as hc:
                        fc = hc.get(forecast_url)
                        if fc.status_code >= 400:
                            last_error = f"forecast fetch failed {fc.status_code}"
                        else:
                            periods = (fc.json().get("properties", {}).get("periods") or [])[: max(1, max_periods)]
                            if periods:
                                data = summarize_periods(periods, location_name, forecast_url, "hourly" if hourly else "period")
                                data["nws_attempts"] = attempts
                                return json.dumps(data, ensure_ascii=False)
                except Exception as fe:
                    last_error = f"forecast fetch exception {fe}"

            # 4) Open-Meteo fallback (전세계 지원) - NWS 실패 시
            try:
                with httpx.Client(timeout=20) as hc:
                    # daily 요약 사용
                    daily_params = "daily=weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max&timezone=auto"
                    resp = hc.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&{daily_params}")
                    if resp.status_code < 400:
                        dj = resp.json()
                        daily = dj.get("daily", {})
                        dates = daily.get("time", [])[:max_periods]
                        wx_codes = daily.get("weather_code", [])[:max_periods]
                        tmax = daily.get("temperature_2m_max", [])[:max_periods]
                        tmin = daily.get("temperature_2m_min", [])[:max_periods]
                        wmax = daily.get("wind_speed_10m_max", [])[:max_periods]
                        code_map = {
                            0: "맑음", 1: "대체로 맑음", 2: "부분적으로 흐림", 3: "흐림",
                            45: "안개", 48: "착빙 안개", 51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
                            61: "약한 비", 63: "비", 65: "강한 비", 71: "약한 눈", 73: "눈", 75: "강한 눈",
                            80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 95: "뇌우", 96: "우박 동반 뇌우",
                        }
                        periods = []
                        bullets = []
                        for i, d in enumerate(dates):
                            desc = code_map.get(wx_codes[i], "날씨") if i < len(wx_codes) else "날씨"
                            tmx = tmax[i] if i < len(tmax) else "?"
                            tmn = tmin[i] if i < len(tmin) else "?"
                            windv = wmax[i] if i < len(wmax) else "?"
                            periods.append({
                                "name": f"Day {i+1}",
                                "date": d,
                                "short": desc,
                                "temp": f"최고 {tmx}°C / 최저 {tmn}°C",
                                "wind": f"최대풍속 {windv} m/s",
                                "start": d,
                                "end": None,
                            })
                            bullets.append(f"- {d}: {desc}, 온도 최고 {tmx}°C / 최저 {tmn}°C, 바람 최대 {windv} m/s")
                        summary_ko = "\n".join(bullets)
                        return json.dumps({
                            "location": location_name,
                            "forecast_type": ("hourly" if hourly else "period") + "/fallback-open-meteo",
                            "forecast": periods,
                            "summary_ko": summary_ko,
                            "source": "open-meteo",
                            "nws_error": last_error,
                            "nws_attempts": attempts,
                            "fallback": True,
                        }, ensure_ascii=False)
            except Exception as fe2:
                last_error = f"nws+fallback failure: {fe2}"

            # 5) 최종 오류 리턴
            snippet = raw_point_text[:400] if raw_point_text else None
            return json.dumps({
                "error": "forecast url missing",
                "details": last_error,
                "nws_attempts": attempts,
                "available_point_keys": list(pjson.keys()) if pjson else [],
                "raw_point_snippet": snippet,
                "hint": "NWS API 실패. 환경변수 NWS_USER_AGENT 에 실제 연락 가능한 이메일 형식 값 설정 후 재시도하거나 다른 도시를 입력하세요.",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    tool = FunctionTool({geocode_city_country, get_weather_summary})
    return tool, {"geocode_city_country": geocode_city_country, "get_weather_summary": get_weather_summary}


def create_agent(client: AIProjectClient, model: str):
    weather_tool, tool_map = build_weather_tool()
    agent = client.agents.create_agent(
        model=model,
        name="weather-agent",
        instructions=(
            "당신은 날씨 안내 에이전트입니다. 사용자는 '국가, 도시' 형식으로 위치를 제공합니다. "
            "절차: 1) geocode_city_country(country, city) 호출 → 위도/경도 획득 2) country_code=US 이면 get_weather_summary(lat, lon) 호출 3) 비미국이면 지원 불가 안내. "
            "get_weather_summary 결과 JSON의 summary_ko 필드는 이미 한국어 bullet 요약입니다. 가능한 경우 summary_ko를 그대로 사용하거나 간단히 다듬어 출력하세요. "
            "JSON에 error 가 있으면 오류 원인을 한 줄로 알려주고, 다른 도시 또는 정확한 철자를 요청하세요."
        ),
        tools=weather_tool.definitions,
    )
    return agent, tool_map


def add_user_message(client: AIProjectClient, thread_id: str, content: str):
    client.agents.messages.create(thread_id=thread_id, role="user", content=content)


def run_agent_with_tools(client: AIProjectClient, agent_id: str, thread_id: str, tool_map: Dict[str, Callable[..., Any]]):
    run = client.agents.runs.create(thread_id=thread_id, agent_id=agent_id)
    print(f"[INFO] Run 시작: run_id={run.id}")
    while True:
        run = client.agents.runs.get(thread_id=thread_id, run_id=run.id)
        status = getattr(run, "status", "?")
        if status in ("failed", "completed", "cancelled", "expired"):
            if status == "failed":
                print(f"[ERROR] Run 실패: {run.last_error}")
            else:
                print(f"[INFO] Run 종료: status={status}")
            break
        if status == "requires_action":
            ra = getattr(run, "required_action", None)
            if ra and getattr(ra, "submit_tool_outputs", None):
                tool_calls = getattr(ra.submit_tool_outputs, "tool_calls", [])
                outputs = []
                for tc in tool_calls:
                    # function 호출만 처리
                    fn = getattr(tc, "function", None)
                    name = getattr(fn, "name", None)
                    args_str = getattr(fn, "arguments", "{}") or "{}"
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        args = {}
                    print(f"[DEBUG] 함수 호출 요청: {name} args={args}")
                    impl = tool_map.get(name)
                    if impl:
                        try:
                            result = impl(**args)
                        except Exception as e:
                            result = json.dumps({"error": str(e)}, ensure_ascii=False)
                        print(f"[DEBUG] 함수 실행 결과(앞 200자): {str(result)[:200]}")
                        outputs.append({"tool_call_id": tc.id, "output": result})
                    else:
                        outputs.append({"tool_call_id": tc.id, "output": json.dumps({"error": "function not implemented"}, ensure_ascii=False)})
                if outputs:
                    client.agents.runs.submit_tool_outputs(
                        thread_id=thread_id,
                        run_id=run.id,
                        tool_outputs=outputs,
                    )
                    print(f"[INFO] 함수 결과 제출: {len(outputs)}개")
        time.sleep(1.2)
    return run


def _extract_text_segments(msg) -> list[str]:  # best-effort across possible SDK shapes
    parts: list[str] = []
    # Newer SDK may expose text_messages
    if getattr(msg, "text_messages", None):
        for tm in msg.text_messages:  # type: ignore[attr-defined]
            if getattr(tm, "text", None) and getattr(tm.text, "value", None):
                parts.append(tm.text.value)
    # Fallback: msg.content (list or str)
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, Iterable):
        for c in content:  # could be tool / text objects
            text_val = getattr(c, "text", None) or getattr(c, "value", None) or getattr(c, "content", None)
            if isinstance(text_val, str):
                parts.append(text_val)
    return [p for p in (p.strip() for p in parts) if p]


def print_conversation(client: AIProjectClient, thread_id: str):
    print("\n=== 대화 로그 (오래된 순) ===")
    messages = list(client.agents.messages.list(thread_id=thread_id))
    messages.reverse()  # API returns newest first; reverse to oldest→newest if needed
    for m in messages:
        segs = _extract_text_segments(m) or ["(no text content)"]
        role = getattr(m, "role", "?")
        if role == MessageRole.AGENT:
            role_label = "에이전트"
        elif role == MessageRole.USER:
            role_label = "사용자"
        else:
            role_label = role
        print(f"[{role_label}]")
        for s in segs:
            print(s)
        print("---")


def main():
    endpoint, model = ensure_env()
    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    keep = os.environ.get("KEEP_AGENT") == "1"

    with client:
        # Step 1: 에이전트 생성
        agent, tool_map = create_agent(client, model)
        print(f"[INFO] 에이전트 생성: id={agent.id}")

        # Step 2: Thread 생성
        thread = client.agents.threads.create()
        print(f"[INFO] 스레드 생성: id={thread.id}")

        # Step 3: 사용자 프롬프트 (국가, 도시 입력 방식)
        user_prompt = "국가와 도시를 입력할게: 미국, 워싱턴 DC 날씨 5개 구간 요약해줘"
        add_user_message(client, thread.id, user_prompt)

        # Step 4: Run 실행 (툴 처리 루프 포함)
        run_agent_with_tools(client, agent.id, thread.id, tool_map)

        # Step 5: 결과 출력
        print_conversation(client, thread.id)

        # Step 6: 에이전트 정리 (KEEP_AGENT=1 설정 시 보존)
        if not keep:
            client.agents.delete_agent(agent.id)
            print("[INFO] 에이전트 삭제 완료 (KEEP_AGENT=1 설정 시 유지 가능).")
        else:
            print("[INFO] KEEP_AGENT=1 → 에이전트 유지됨.")


if __name__ == "__main__":
    main()
