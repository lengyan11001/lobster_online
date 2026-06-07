"""
澶фā鍨嬪鎴风 - 鐢ㄤ簬楂樻剰鍚戣瘎璁虹瓫閫?"""
import json
import re
from typing import Any, Callable, Dict, List, Optional

import requests


def _safe_event_log(event_logger: Optional[Callable], event: str, **fields) -> None:
    if event_logger is None:
        return
    try:
        event_logger(event, **fields)
    except Exception:
        pass

DEFAULT_MODEL = "gpt-5.4"

# 绛涢€夎瘎璁虹殑绯荤粺鎻愮ず璇嶏紙绱㈠紩杈撳嚭锛岄伩鍏嶄涪澶辩敤鎴蜂富閿級
FILTER_PROMPT_SYSTEM = """浣犳槸涓€涓皬绾功杩愯惀涓撳銆傝鍒嗘瀽璇勮锛岀瓫閫夐珮鎰忓悜鐢ㄦ埛銆?
楂樻剰鍚戝鎴风壒寰侊細
1. 璇㈤棶浠锋牸銆佽垂鐢ㄣ€佹姤浠?2. 璇㈤棶濡備綍鎶ュ悕銆佸浣曡喘涔?3. 琛ㄨ揪寮虹儓鍏磋叮锛屾兂浜嗚В鏇村
4. 璇㈤棶鍏蜂綋缁嗚妭锛堣绋嬪唴瀹广€佹晥鏋溿€佹椂闂寸瓑锛?5. 鏈夋槑纭渶姹傦紝鎻忚堪鑷繁鎯呭喌

涓ユ牸杩斿洖JSON锛屾牸寮忓涓嬶細
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "绛涢€夌悊鐢?,
            "score": 0.0
        }
    ]
}

瑕佹眰锛?- comment_index 涓鸿瘎璁哄垪琛ㄤ腑鐨勫簭鍙凤紙浠?寮€濮嬶級
- score 鑼冨洿 0~1
- 浠呰繑鍥?high 鍜?medium 鎰忓悜
- 涓嶈杩斿洖鍒楄〃閲屼笉瀛樺湪鐨勫簭鍙?- 鍙繑鍥?JSON锛屼笉瑕佷换浣曢澶栨枃鏈?"""

DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM = """浣犳槸涓€涓姈闊宠瘎璁虹瓫閫夊姪鎵嬨€?
浣犵殑浠诲姟涓嶆槸濂楃敤鍥哄畾鐨勨€滃己鎴愪氦鈥濇爣鍑嗭紝鑰屾槸浼樺厛鎵ц鐢ㄦ埛鎻愪緵鐨勨€滅簿鍑嗗鎴风瓫閫夋彁绀鸿瘝鈥濄€?濡傛灉鐢ㄦ埛缁欎簡绛涢€夋彁绀鸿瘝锛屽繀椤讳互閭ｄ唤鎻愮ず璇嶄负鏈€楂樹紭鍏堢骇锛屼笉瑕佸啀棰濆濂楃敤鏇翠弗鏍肩殑闅愯棌瑙勫垯銆?鍙渶瑕佸垽鏂€滆繖鏉¤瘎璁烘槸涓嶆槸绮惧噯瀹㈡埛鈥濓紝涓嶈鍋氬垎灞傦紝涓嶈绉佽嚜鎷嗘垚寮哄急绛夌骇銆?绗﹀悎绛涢€夋彁绀鸿瘝鐨勮瘎璁哄氨淇濈暀锛屼笉绗﹀悎灏变笉瑕佽繑鍥炪€?
涓ユ牸杩斿洖JSON锛屾牸寮忓涓嬶細
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "绛涢€夌悊鐢?,
            "score": 0.0
        }
    ]
}

瑕佹眰锛?- comment_index 涓鸿瘎璁哄垪琛ㄤ腑鐨勫簭鍙凤紙浠?寮€濮嬶級
- score 鑼冨洿 0~1
- 濡傛灉鍒ゅ畾涓虹簿鍑嗗鎴凤紝intent_level 缁熶竴杩斿洖 high
- 涓嶈杩斿洖鍒楄〃閲屼笉瀛樺湪鐨勫簭鍙?- 鍙繑鍥?JSON锛屼笉瑕佷换浣曢澶栨枃鏈?"""

DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM = """浣犳槸涓€涓姈闊宠瘎璁哄弽鍚戠瓫閫夊姪鎵嬨€?
浣犵殑浠诲姟鏄敖閲忎繚鐣欌€滃拰褰撳墠瑙嗛涓婚鐩稿叧銆佷笖鏈夋剰涔夌殑浜掑姩璇勮鈥濓紝鍙帓闄ゆ槑鏄句笉搴旇杩涘叆绮惧噯瀹㈡埛鐨勫唴瀹广€?鍙渶瑕佸垽鏂€滆繖鏉¤瘎璁烘槸涓嶆槸绮惧噯瀹㈡埛鈥濓紝涓嶈鍋氬垎灞傦紝涓嶈绉佽嚜鎷嗘垚寮哄急绛夌骇銆?
浠ヤ笅鍐呭搴旀帓闄わ細
1. 绾〃鎯呫€佺函绗﹀彿銆佺函璇皵璇嶃€佺函鏃犳剰涔夌煭鍙?2. 绾矾杩囥€佹墦鍗°€佸搱鍝堛€佹敮鎸併€佷笉閿欍€佹潵浜嗚繖绫绘棤瀹為檯淇℃伅鐨勮瘎璁?3. 绾颈楠傘€佹敾鍑汇€侀槾闃虫€皵銆佷汉韬敾鍑?4. 瀹屽叏鏃犲叧鍐呭銆佸箍鍛婂埛灞忋€佹伓鎰忕亴姘?5. 铏界劧鏄甯歌璇濓紝浣嗗拰褰撳墠瑙嗛鏍囬銆佸綋鍓嶈瘽棰樸€佸綋鍓嶆悳绱㈠叧閿瘝鏄庢樉鏃犲叧鐨勯棽鑱婏紝渚嬪闂悆浜嗗悧銆佺┛浠€涔堛€佸湪骞插槢杩欑被璺戦鍐呭

浠ヤ笅鍐呭搴斾繚鐣欙細
1. 鏈夌湡瀹炶〃杈俱€佺湡瀹炶鐐广€佺湡瀹為棶棰橈紝涓斿拰褰撳墠瑙嗛涓婚鐩稿叧鐨勮瘎璁?2. 琛ㄨ揪闇€姹傘€佸叴瓒ｃ€佸挩璇€佷簡瑙ｃ€佽仈绯汇€佸皾璇曘€佸悎浣溿€佽喘涔版剰鎰跨殑璇勮
3. 铏界劧娌℃湁鐩存帴鎴愪氦锛屼絾鏄庢樉鏄湪鍥寸粫褰撳墠瑙嗛鍐呭璁ょ湡浜掑姩銆佽鐪熸彁闂€佽鐪熻〃杈炬兂娉曠殑璇勮

涓ユ牸杩斿洖JSON锛屾牸寮忓涓嬶細
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "绛涢€夌悊鐢?,
            "score": 0.0
        }
    ]
}

瑕佹眰锛?- comment_index 涓鸿瘎璁哄垪琛ㄤ腑鐨勫簭鍙凤紙浠?寮€濮嬶級
- score 鑼冨洿 0~1
- 濡傛灉鍒ゅ畾涓虹簿鍑嗗鎴凤紝intent_level 缁熶竴杩斿洖 high
- 涓嶈杩斿洖鍒楄〃閲屼笉瀛樺湪鐨勫簭鍙?- 鍙繑鍥?JSON锛屼笉瑕佷换浣曢澶栨枃鏈?"""

XHS_FILTER_PROMPT_SYSTEM = """浣犳槸涓€涓皬绾功璇勮绛涢€夊姪鎵嬶紝璐熻矗浠庤瘎璁洪噷鎵惧嚭鍊煎緱缁х画璺熻繘鐨勬綔鍦ㄥ鎴枫€?
鎵ц瑙勫垯锛?1. 濡傛灉鐢ㄦ埛鎻愪緵浜嗏€滈珮鎰忓悜绛涢€夋彁绀鸿瘝鈥濓紝蹇呴』鎶婇偅浠芥彁绀鸿瘝瑙嗕负鏈€楂樹紭鍏堢骇锛屾寜鐢ㄦ埛瀹氫箟鐨勪汉缇ゅ彛寰勭瓫閫夈€?2. 鍙璇勮鏄庢樉绗﹀悎鐢ㄦ埛鎻愮ず璇嶏紝灏卞彲浠ヤ繚鐣欙紱涓嶈棰濆寮鸿瑕佹眰蹇呴』鍑虹幇浠锋牸銆佹姤鍚嶃€佽喘涔扮瓑瀛楁牱銆?3. 濡傛灉鐢ㄦ埛娌℃湁鎻愪緵鑷畾涔夋彁绀鸿瘝锛屽啀鍙傝€冮粯璁ら珮鎰忓悜鏍囧噯锛?   - 璇㈤棶浠锋牸銆佽垂鐢ㄣ€佹姤浠?   - 璇㈤棶濡備綍鎶ュ悕銆佸浣曡喘涔般€佹€庝箞寮€濮?   - 琛ㄨ揪鏄庣‘鍏磋叮锛屾兂杩涗竴姝ヤ簡瑙?   - 璇㈤棶鍏蜂綋缁嗚妭銆佹晥鏋溿€佹祦绋嬨€侀€傚悎浜虹兢
   - 鎻忚堪鑷韩闇€姹傦紝甯屾湜鑾峰緱鏂规鎴栧府鍔?4. 鎷夸笉鍑嗕絾鏄庢樉鍊煎緱缁х画璺熻繘鐨勶紝涔熷彲浠ヤ繚鐣欏苟鏍囪涓?medium锛屼笉瑕佸洜涓轰笉澶熲€滃己鎴愪氦鈥濆氨鍏ㄩ儴杩囨护鎺夈€?
涓ユ牸杩斿洖 JSON锛屾牸寮忓涓嬶細
{
  "high_intent_refs": [
    {
      "comment_index": 1,
      "intent_level": "high/medium/low",
      "reason": "绛涢€夌悊鐢?,
      "score": 0.0
    }
  ]
}

瑕佹眰锛?- comment_index 鏄瘎璁哄垪琛ㄤ腑鐨勫簭鍙凤紝浠?1 寮€濮?- score 鑼冨洿 0~1
- 鍙繑鍥炰綘鍐冲畾淇濈暀鐨勮瘎璁?- 涓嶈杩斿洖鍒楄〃閲屼笉瀛樺湪鐨勫簭鍙?- 鍙繑鍥?JSON锛屼笉瑕侀檮鍔犺В閲婃枃鏈?""

class AIClient:
    """澶фā鍨嬪鎴风"""

    def __init__(self, api_url: str, api_key: str, model: str = DEFAULT_MODEL):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def filter_comments(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        鐢ˋI绛涢€夐珮鎰忓悜璇勮

        Args:
            post_title: 甯栧瓙鏍囬
            comments: 璇勮鍒楄〃 [{"username": "鐢ㄦ埛", "content": "璇勮"}]
            direction: 璇勮鐢熸垚鏂瑰悜锛堝彲閫夛級

        Returns:
            楂樻剰鍚戠敤鎴峰垪琛紙淇濈暀 user_id/xsec_token/comment_id 绛変富閿級
        """
        if not comments:
            return []

        batch_size = 80
        merged: List[Dict] = []
        seen = set()

        total_batches = (len(comments) + batch_size - 1) // batch_size
        _safe_event_log(
            event_logger,
            "ai_filter_invoke",
            post_title=post_title,
            comments_total=len(comments),
            batch_size=batch_size,
            total_batches=total_batches,
            intent_profile=intent_profile,
            filter_strategy=filter_strategy,
            direction=direction,
            custom_prompt=custom_prompt,
        )

        for start in range(0, len(comments), batch_size):
            candidate_comments = comments[start:start + batch_size]
            batch_result = self._filter_comments_batch(
                post_title,
                candidate_comments,
                direction,
                intent_profile=intent_profile,
                custom_prompt=custom_prompt,
                filter_strategy=filter_strategy,
                event_logger=event_logger,
                batch_index=start // batch_size + 1,
                total_batches=total_batches,
            )
            for row in batch_result:
                key = row.get("comment_id") or f"{row.get('user_id', '')}|{row.get('content', '')}|{row.get('comment_time', '')}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)

        return merged

    def _filter_comments_batch(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
        batch_index: int = 1,
        total_batches: int = 1,
    ) -> List[Dict]:
        if not comments:
            return []
        return self._filter_comments_batch_v2(
            post_title,
            comments,
            direction=direction,
            intent_profile=intent_profile,
            custom_prompt=custom_prompt,
            filter_strategy=filter_strategy,
            event_logger=event_logger,
            batch_index=batch_index,
            total_batches=total_batches,
        )

        candidate_comments = comments
        lines = []
        for i, c in enumerate(candidate_comments, start=1):
            lines.append(
                f"{i}. username={c.get('username', '')} | "
                f"user_id={c.get('user_id', '')} | "
                f"content={c.get('content', '')}"
            )

        is_douyin_transactional = intent_profile == "douyin_transactional"
        is_reverse_filter = is_douyin_transactional and str(filter_strategy or "prompt").strip().lower() == "reverse"
        custom_prompt_text = str(custom_prompt or "").strip()
        direction_text = (
            f"\n鏈绮惧噯瀹㈡埛绛涢€夋彁绀鸿瘝锛堟渶楂樹紭鍏堢骇锛岀洿鎺ュ喅瀹氱瓫閫夊彛寰勶級:\n{direction}"
            if direction and is_douyin_transactional and not is_reverse_filter
            else (f"\n璇勮鏂瑰悜鍙傝€? {direction}" if direction else "")
        )
        reverse_rule_text = (
            f"\n鏈鍙嶅悜绛涢€夎ˉ鍏呰鏄庯紙鍙€夊弬鑰冿級:\n{direction}"
            if direction and is_reverse_filter
            else ""
        )
        extra_rule_text = ""
        if custom_prompt_text and not is_douyin_transactional:
            extra_rule_text = f"\n鏈楂樻剰鍚戠瓫閫夐澶栬鍒欙紙浼樺厛鎸夋鎵ц锛?\n{custom_prompt_text}"
        user_prompt = f"""甯栧瓙鏍囬: {post_title}

璇勮鍒楄〃:
{chr(10).join(lines)}{direction_text}{reverse_rule_text}{extra_rule_text}

{"璇蜂弗鏍兼寜涓婇潰鐨勭瓫閫夋彁绀鸿瘝绛涢€夎瘎璁猴紝鍙垽鏂槸鍚﹀睘浜庣簿鍑嗗鎴凤紱绗﹀悎灏辫繑鍥烇紝涓嶇鍚堜笉瑕佽繑鍥烇紱杩斿洖缁撴灉閲岀殑 intent_level 缁熶竴鍐?high锛屽苟杩斿洖JSON銆? if is_douyin_transactional and not is_reverse_filter else ("璇锋寜鍙嶅悜绛涢€夎鍒欏鐞嗭細鍙繚鐣欏拰褰撳墠瑙嗛涓婚鐩稿叧鐨勬湁鏁堜簰鍔紱鎺掗櫎鏃犳剰涔夈€佽〃鎯呫€佹敾鍑汇€佺亴姘达紝浠ュ強鍜岃棰戞爣棰樻垨褰撳墠璇濋鏄庢樉鏃犲叧鐨勯棽鑱婅瘎璁猴紱杩斿洖缁撴灉閲岀殑 intent_level 缁熶竴鍐?high锛屽苟杩斿洖JSON銆? if is_reverse_filter else "璇风瓫閫夐珮鎰忓悜鐢ㄦ埛骞惰繑鍥濲SON銆?)}"""

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM
                        if is_reverse_filter
                        else DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM
                        if is_douyin_transactional
                        else FILTER_PROMPT_SYSTEM
                    ),
                },
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 2000
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                data = self._parse_json_content(content)
                refs = data.get("high_intent_refs") if isinstance(data, dict) else None
                if isinstance(refs, list):
                    mapped = self._map_refs_to_comments(refs, candidate_comments)
                    if is_reverse_filter:
                        return self._merge_comment_rows(
                            mapped,
                            self._reverse_filter_comments(candidate_comments, post_title),
                        )
                    return mapped
                return self._fallback_filter(
                    candidate_comments,
                    intent_profile=intent_profile,
                    filter_strategy=filter_strategy,
                    post_title=post_title,
                )
            else:
                print(f"API閿欒: {response.status_code}, body={response.text[:500]}")
                return self._fallback_filter(
                    candidate_comments,
                    intent_profile=intent_profile,
                    filter_strategy=filter_strategy,
                    post_title=post_title,
                )

        except Exception as e:
            print(f"璋冪敤AI澶辫触: {str(e)}")
            return self._fallback_filter(
                candidate_comments,
                intent_profile=intent_profile,
                filter_strategy=filter_strategy,
                post_title=post_title,
            )

    def _filter_comments_batch_v2(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
        batch_index: int = 1,
        total_batches: int = 1,
    ) -> List[Dict]:
        candidate_comments = comments
        lines = []
        for i, c in enumerate(candidate_comments, start=1):
            lines.append(
                f"{i}. username={c.get('username', '')} | "
                f"user_id={c.get('user_id', '')} | "
                f"content={c.get('content', '')}"
            )

        is_douyin_transactional = intent_profile == "douyin_transactional"
        is_reverse_filter = is_douyin_transactional and str(filter_strategy or "prompt").strip().lower() == "reverse"
        custom_prompt_text = str(custom_prompt or "").strip()
        direction_text = (
            f"\n鏈绮惧噯瀹㈡埛绛涢€夋彁绀鸿瘝锛堟渶楂樹紭鍏堢骇锛岀洿鎺ュ喅瀹氱瓫閫夊彛寰勶級:\n{direction}"
            if direction and is_douyin_transactional and not is_reverse_filter
            else ""
        )
        reverse_rule_text = (
            f"\n鏈鍙嶅悜绛涢€夎ˉ鍏呰鏄庯紙鍙€夊弬鑰冿級:\n{direction}"
            if direction and is_reverse_filter
            else ""
        )
        extra_rule_text = ""
        if custom_prompt_text and not is_douyin_transactional:
            extra_rule_text = (
                "\n鏈楂樻剰鍚戠瓫閫夋彁绀鸿瘝锛堟渶楂樹紭鍏堢骇锛岃涓ユ牸鎸夎繖浠藉彛寰勭瓫閫夛紱"
                "鍙鏄庢樉绗﹀悎灏卞彲浠ヤ繚鐣欙紝涓嶈鍐嶅鐢ㄦ洿涓ユ牸鐨勯殣钘忔爣鍑嗭級:\n"
                f"{custom_prompt_text}"
            )

        user_prompt = f"""甯栧瓙鏍囬: {post_title}

璇勮鍒楄〃:
{chr(10).join(lines)}{direction_text}{reverse_rule_text}{extra_rule_text}

{"璇蜂弗鏍兼寜涓婇潰鐨勭瓫閫夋彁绀鸿瘝绛涢€夎瘎璁猴紝鍙垽鏂槸鍚﹀睘浜庣簿鍑嗗鎴凤紱绗﹀悎灏辫繑鍥烇紝涓嶇鍚堜笉瑕佽繑鍥烇紱杩斿洖缁撴灉閲岀殑 intent_level 缁熶竴鍐?high锛屽苟杩斿洖 JSON銆? if is_douyin_transactional and not is_reverse_filter else ("璇锋寜鍙嶅悜绛涢€夎鍒欏鐞嗭細鍙繚鐣欏拰褰撳墠瑙嗛涓婚鐩稿叧鐨勬湁鏁堜簰鍔紱鎺掗櫎鏃犳剰涔夈€佽〃鎯呫€佹敾鍑汇€佺亴姘达紝浠ュ強鍜岃棰戞爣棰樻垨褰撳墠璇濋鏄庢樉鏃犲叧鐨勯棽鑱婅瘎璁猴紱杩斿洖缁撴灉閲岀殑 intent_level 缁熶竴鍐?high锛屽苟杩斿洖 JSON銆? if is_reverse_filter else ("濡傛灉涓婇潰鎻愪緵浜嗏€滈珮鎰忓悜绛涢€夋彁绀鸿瘝鈥濓紝璇锋妸瀹冨綋浣滄渶楂樹紭鍏堢骇锛涘彧瑕佹槑鏄剧鍚堟彁绀鸿瘝灏变繚鐣欙紝鎷夸笉鍑嗕絾鍊煎緱缁х画璺熻繘鐨勪篃鍙互淇濈暀涓?medium銆傝绛涢€夐珮鎰忓悜鐢ㄦ埛骞惰繑鍥?JSON銆? if custom_prompt_text else "璇锋牴鎹粯璁ら珮鎰忓悜鏍囧噯绛涢€夊€煎緱缁х画璺熻繘鐨勭敤鎴凤紝骞惰繑鍥?JSON銆?))}"""

        system_prompt_name = (
            "DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM"
            if is_reverse_filter
            else "DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM"
            if is_douyin_transactional
            else "XHS_FILTER_PROMPT_SYSTEM"
        )
        system_prompt = (
            DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM
            if is_reverse_filter
            else DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM
            if is_douyin_transactional
            else XHS_FILTER_PROMPT_SYSTEM
        )

        audit_comments = [
            {
                "comment_index": i,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "content": c.get("content", ""),
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location") or c.get("region") or c.get("ip_location") or "",
                "profile_url": c.get("profile_url", ""),
            }
            for i, c in enumerate(candidate_comments, start=1)
        ]

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 2000,
        }

        _safe_event_log(
            event_logger,
            "ai_request",
            batch_index=batch_index,
            total_batches=total_batches,
            comments_in_batch=len(candidate_comments),
            model=self.model,
            api_url=self.api_url,
            system_prompt_name=system_prompt_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            input_comments=audit_comments,
            direction=direction,
            custom_prompt=custom_prompt_text,
            is_douyin_transactional=is_douyin_transactional,
            is_reverse_filter=is_reverse_filter,
        )

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=60,
            )
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                data = self._parse_json_content(content)
                refs = data.get("high_intent_refs") if isinstance(data, dict) else None
                _safe_event_log(
                    event_logger,
                    "ai_response",
                    batch_index=batch_index,
                    total_batches=total_batches,
                    http_status=response.status_code,
                    raw_response=content,
                    parsed_refs=refs if isinstance(refs, list) else [],
                    refs_count=len(refs) if isinstance(refs, list) else 0,
                    parsed_ok=isinstance(refs, list),
                )
                if isinstance(refs, list):
                    mapped = self._map_refs_to_comments_v2(
                        refs,
                        candidate_comments,
                        allow_low=bool(custom_prompt_text and not is_douyin_transactional),
                    )
                    _safe_event_log(
                        event_logger,
                        "ai_mapped",
                        batch_index=batch_index,
                        total_batches=total_batches,
                        refs_count=len(refs),
                        mapped_count=len(mapped),
                        mapped_users=[
                            {
                                "comment_index": row.get("comment_index", ""),
                                "username": row.get("username", ""),
                                "user_id": row.get("user_id", ""),
                                "content": row.get("content", ""),
                                "intent_level": row.get("intent_level", ""),
                                "intent_reason": row.get("intent_reason", ""),
                                "intent_score": row.get("intent_score", ""),
                            }
                            for row in mapped
                        ],
                        is_reverse_filter=is_reverse_filter,
                    )
                    if is_reverse_filter:
                        merged = self._merge_comment_rows(
                            mapped,
                            self._reverse_filter_comments(candidate_comments, post_title),
                        )
                        _safe_event_log(
                            event_logger,
                            "ai_reverse_merged",
                            batch_index=batch_index,
                            mapped_count=len(mapped),
                            merged_count=len(merged),
                        )
                        return merged
                    return mapped
            else:
                _safe_event_log(
                    event_logger,
                    "ai_http_error",
                    batch_index=batch_index,
                    http_status=response.status_code,
                    raw_response=response.text[:600],
                )
                print(f"API閿欒: {response.status_code}, body={response.text[:500]}")
        except Exception as e:
            _safe_event_log(
                event_logger,
                "ai_exception",
                batch_index=batch_index,
                error=str(e),
            )
            print(f"璋冪敤AI澶辫触: {str(e)}")

        fallback = self._fallback_filter_v2(
            candidate_comments,
            intent_profile=intent_profile,
            filter_strategy=filter_strategy,
            post_title=post_title,
            custom_prompt=custom_prompt_text,
        )
        _safe_event_log(
            event_logger,
            "ai_fallback",
            batch_index=batch_index,
            total_batches=total_batches,
            comments_in_batch=len(candidate_comments),
            fallback_count=len(fallback),
            fallback_used=True,
        )
        return fallback

    def _parse_json_content(self, content: str) -> Dict[str, Any]:
        """浠庢ā鍨嬭繑鍥炰腑灏介噺瑙ｆ瀽鍑篔SON瀵硅薄銆?""
        if not content:
            return {}

        text = content.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return {}
        return {}

    def _map_refs_to_comments_v2(
        self,
        refs: List[Dict[str, Any]],
        comments: List[Dict],
        *,
        allow_low: bool = False,
    ) -> List[Dict]:
        mapped: List[Dict] = []
        seen = set()
        allowed_levels = {"high", "medium", "low"} if allow_low else {"high", "medium"}

        for item in refs:
            try:
                idx = int(item.get("comment_index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(comments):
                continue

            source = comments[idx]
            key = source.get("comment_id") or f"{source.get('user_id', '')}|{source.get('content', '')}|{source.get('comment_time', '')}"
            if key in seen:
                continue

            level = str(item.get("intent_level", "medium")).lower()
            if level not in allowed_levels:
                continue
            normalized_level = "medium" if allow_low and level == "low" else level

            mapped.append({
                "comment_index": idx + 1,
                "username": source.get("username", ""),
                "user_id": source.get("user_id", ""),
                "user_xsec_token": source.get("user_xsec_token", ""),
                "comment_id": source.get("comment_id", ""),
                "comment": source.get("content", ""),
                "content": source.get("content", ""),
                "comment_time": source.get("comment_time", ""),
                "location": source.get("location", source.get("ip_location", "")),
                "ip_location": source.get("ip_location", source.get("location", "")),
                "like_count": source.get("like_count", ""),
                "reply_count": source.get("reply_count", ""),
                "profile_url": source.get("profile_url", ""),
                "avatar_url": source.get("avatar_url", ""),
                "reason": item.get("reason", ""),
                "intent_level": normalized_level,
                "score": item.get("score", 0),
            })
            seen.add(key)

        return mapped

    def _map_refs_to_comments(self, refs: List[Dict[str, Any]], comments: List[Dict]) -> List[Dict]:
        """灏嗘ā鍨嬭繑鍥炵殑 comment_index 鏄犲皠鍥炲師濮嬭瘎璁哄璞°€?""
        if not refs:
            return []

        mapped: List[Dict] = []
        seen = set()
        for item in refs:
            try:
                idx = int(item.get("comment_index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(comments):
                continue

            source = comments[idx]
            key = source.get("comment_id") or f"{source.get('user_id', '')}|{source.get('content', '')}|{source.get('comment_time', '')}"
            if key in seen:
                continue

            level = str(item.get("intent_level", "medium")).lower()
            if level not in ("high", "medium"):
                continue

            mapped.append({
                "comment_index": idx + 1,
                "username": source.get("username", ""),
                "user_id": source.get("user_id", ""),
                "user_xsec_token": source.get("user_xsec_token", ""),
                "comment_id": source.get("comment_id", ""),
                "comment": source.get("content", ""),
                "content": source.get("content", ""),
                "comment_time": source.get("comment_time", ""),
                "location": source.get("location", source.get("ip_location", "")),
                "ip_location": source.get("ip_location", source.get("location", "")),
                "like_count": source.get("like_count", ""),
                "reply_count": source.get("reply_count", ""),
                "profile_url": source.get("profile_url", ""),
                "avatar_url": source.get("avatar_url", ""),
                "reason": item.get("reason", ""),
                "intent_level": level,
                "score": item.get("score", 0),
            })
            seen.add(key)

        return mapped

    def _merge_comment_rows(self, primary: List[Dict], extra: List[Dict]) -> List[Dict]:
        """鍚堝苟 AI 缁撴灉涓庡彫鍥炵粨鏋滐紝浼樺厛淇濈暀 AI 鍛戒腑鐨勫師濮嬬悊鐢便€?""
        merged: List[Dict] = []
        seen = set()
        for row in list(primary or []) + list(extra or []):
            key = row.get("comment_id") or f"{row.get('user_id', '')}|{row.get('content', '')}|{row.get('comment_time', '')}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    def _extract_prompt_keywords(self, custom_prompt: str) -> tuple[str, ...]:
        text = str(custom_prompt or "").strip().lower()
        if not text:
            return ()

        stopwords = {
            "楂樻剰鍚?, "绮惧噯瀹㈡埛", "瀹㈡埛", "鐢ㄦ埛", "璇勮", "鍐呭", "鐨勪汉", "鍙互", "闇€瑕?, "杩涜",
            "绛涢€?, "绗﹀悎", "灞炰簬", "浼樺厛", "淇濈暀", "涓嶈", "鎺掗櫎", "鎻愮ず璇?, "鍙ｅ緞", "鏍囧噯",
        }
        tokens: List[str] = []
        for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,12}", text):
            token = token.strip()
            if not token or token.isdigit() or token in stopwords:
                continue
            tokens.append(token)
        return tuple(dict.fromkeys(tokens))

    def _fallback_filter_v2(
        self,
        comments: List[Dict],
        intent_profile: str = "default",
        filter_strategy: str = "prompt",
        post_title: str = "",
        custom_prompt: str = "",
    ) -> List[Dict]:
        if intent_profile == "douyin_transactional" and str(filter_strategy or "prompt").strip().lower() == "reverse":
            return self._reverse_filter_comments(comments, post_title)
        if intent_profile == "douyin_transactional":
            return self._fallback_filter(
                comments,
                intent_profile=intent_profile,
                filter_strategy=filter_strategy,
                post_title=post_title,
            )

        prompt_keywords = self._extract_prompt_keywords(custom_prompt)
        keywords = (
            "澶氬皯", "浠锋牸", "鎶ヤ环", "鎬庝箞", "濡備綍", "鍜ㄨ", "鑱旂郴鏂瑰紡", "鍙互鍚?, "鍚?, "锛?, "?",
            "浜嗚В", "鎯充簡瑙?, "娴佺▼", "缁嗚妭", "璧勬枡", "鏂规", "閫傚悎", "鎬庝箞鍋?, "鎬庝箞寮€濮?,
            "鎯宠瘯璇?, "闇€瑕?, "闇€姹?,
        )
        if prompt_keywords:
            keywords = tuple(dict.fromkeys(list(keywords) + list(prompt_keywords)))

        fallback = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", ""))
            compact_content = "".join(content.split())
            if not compact_content:
                continue
            if not any(k in compact_content for k in keywords):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            fallback.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": "鍏抽敭璇嶅厹搴曠瓫閫?,
                "intent_level": "medium",
                "score": 0.55,
            })
            seen.add(key)
        return fallback

    def _fallback_filter(
        self,
        comments: List[Dict],
        intent_profile: str = "default",
        filter_strategy: str = "prompt",
        post_title: str = "",
    ) -> List[Dict]:
        """
        褰撴ā鍨嬭繑鍥炲紓甯告椂浣跨敤鍏抽敭璇嶅厹搴曪紝閬垮厤鏁存壒涓㈠け銆?        浠呬綔涓轰繚搴曠瓥鐣ワ紝浼樺厛浣跨敤妯″瀷缁撴灉銆?        """
        if intent_profile == "douyin_transactional" and str(filter_strategy or "prompt").strip().lower() == "reverse":
            return self._reverse_filter_comments(comments, post_title)
        if intent_profile == "douyin_transactional":
            keywords = (
                "澶氬皯閽?, "浠锋牸", "璐圭敤", "鏀惰垂", "鎶ヤ环", "濂楅",
                "鎬庝箞涔?, "鎬庝箞涓嬪崟", "鎬庝箞璐拱", "鎬庝箞鎶ュ悕", "鍝噷鎶ュ悕", "鎯虫姤鍚?, "鎯充拱", "涓嬪崟",
                "鎬庝箞鍚堜綔", "鍚堜綔", "鍟嗗姟鍚堜綔", "鍔犵洘", "浠ｇ悊",
                "鎬庝箞鑱旂郴", "鑱旂郴鏂瑰紡", "姹傝仈绯绘柟寮?, "鐢佃瘽", "寰俊", "绉佷俊", "绉佽亰", "瀵规帴",
                "鍜ㄨ", "鎯冲挩璇?, "鎯充簡瑙?, "浜嗚В涓€涓?, "璇︾粏鑱婅亰", "缁欎釜鏂规", "鏈夋病鏈夋柟妗?,
                "鎴戦渶瑕?, "鎴戞兂", "閫傚悎鎴戝悧", "閫備笉閫傚悎鎴?, "鑳戒笉鑳藉仛", "鍙互鍋氬悧",
                "鎰熷叴瓒?, "鏈夊叴瓒?, "鎯宠瘯璇?, "鎯冲仛", "鎬庝箞寮?, "鎬庝箞鎼?, "鎬庝箞寮€濮?,
                "鎯冲叆鎵?, "鍏ユ墜", "鑳戒笅鎵嬪悧", "鑳戒笉鑳戒拱", "鍙互涔板悧", "鍙互鍏ュ悧",
                "鏈夋病鏈?, "鏈夊悧", "鎬庝箞閫?, "鎺ㄨ崘涓€涓?, "鍥炲鎴?, "鍥炴垜涓€涓?,
            )
            exclude_keywords = ()
            fallback_reason = "鎶栭煶鎰忓悜鍏抽敭璇嶅厹搴曠瓫閫?
            fallback_score = 0.6
        else:
            keywords = ("澶氬皯", "浠锋牸", "鎶ヤ环", "鎬庝箞", "濡備綍", "鍜ㄨ", "鑱旂郴鏂瑰紡", "鍙互鍚?, "鍚?, "锛?, "?")
            exclude_keywords = ()
            fallback_reason = "鍏抽敭璇嶅厹搴曠瓫閫?
            fallback_score = 0.55
        fallback = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", ""))
            compact_content = "".join(content.split())
            if not content:
                continue
            if exclude_keywords and any(k in compact_content for k in exclude_keywords):
                continue
            if not any(k in compact_content for k in keywords):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            fallback.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": fallback_reason,
                "intent_level": "medium",
                "score": fallback_score,
            })
            seen.add(key)
        return fallback

    def _extract_topic_tokens(self, text: str) -> set[str]:
        raw = str(text or "").strip().lower()
        if not raw:
            return set()
        stopwords = {
            "浠€涔?, "鎬庝箞", "鍙互", "涓€涓?, "涓€涓?, "杩欎釜", "閭ｄ釜", "鐪熺殑", "灏辨槸", "鏈夋病鏈?,
            "瑙嗛", "浣滃搧", "鍐呭", "鍏充簬", "鍒嗕韩", "鎺ㄨ崘", "鐪嬬湅", "浣犱滑", "鎴戜滑", "浠栦滑",
        }
        tokens: set[str] = set()
        for part in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", raw):
            if re.fullmatch(r"[A-Za-z0-9]+", part):
                if len(part) >= 2:
                    tokens.add(part)
                continue
            if len(part) >= 2 and part not in stopwords:
                tokens.add(part)
            for size in (4, 3, 2):
                if len(part) < size:
                    continue
                for index in range(0, len(part) - size + 1):
                    token = part[index:index + size]
                    if token not in stopwords:
                        tokens.add(token)
        return tokens

    def _is_topic_related_comment(self, content: str, post_title: str) -> bool:
        compact_content = "".join(str(content or "").lower().split())
        compact_title = "".join(str(post_title or "").lower().split())
        if not compact_title:
            return True
        content_tokens = self._extract_topic_tokens(compact_content)
        title_tokens = self._extract_topic_tokens(compact_title)
        if not content_tokens or not title_tokens:
            return False
        if content_tokens & title_tokens:
            return True
        return any(token in compact_content for token in title_tokens if len(token) >= 2)

    def _reverse_filter_comments(self, comments: List[Dict], post_title: str = "") -> List[Dict]:
        trivial_exact = {
            "鍝堝搱", "鍝堝搱鍝?, "鍛靛懙", "鍝?, "鍡?, "濂界殑", "鏀跺埌", "鏉ヤ簡", "璺繃", "鎵撳崱", "鏀寔",
            "涓嶉敊", "鐪熷ソ", "鐗?, "鍘夊", "璧?, "濂?, "濂界湅", "鐪嬬湅", "瀛﹀埌浜?, "鏀惰棌浜?,
            "鍏堟敹钘?, "婊存淮", "鍦ㄥ悧", "鍥炴垜", "鍥炰竴涓?, "鍥炲涓€涓?,
        }
        off_topic_chat_exact = {
            "鍚冧簡鍚?, "鍚冮キ浜嗗悧", "绌夸粈涔?, "绌垮暐", "鍦ㄥ共鍢?, "骞插槢鍛?, "鐫′簡鍚?, "鏃╁畨", "鏅氬畨",
            "鍑犵偣鐫?, "蹇欎粈涔?, "绾﹀悧", "澶氬ぇ浜?, "缁撳浜嗗悧", "甯呬笉甯?, "缇庝笉缇?,
        }
        abusive_keywords = (
            "楠楀瓙", "楠椾汉", "鍨冨溇", "婊?, "鏈夌梾", "鏅哄晢绋?, "鑴戞畫", "鍌?, "瑁呴€?, "鎵贰", "鑳¤",
            "鍧戜汉", "榛戝簵", "鍘绘", "鎭跺績", "搴熺墿",
        )
        intent_keywords = (
            "浠锋牸", "璐圭敤", "鏀惰垂", "鎶ヤ环", "澶氬皯閽?, "鎬庝箞鍗?, "鎬庝箞涔?, "鎬庝箞涓嬪崟", "鎬庝箞璐拱",
            "鎬庝箞鎶ュ悕", "鍚堜綔", "鍔犵洘", "浠ｇ悊", "鑱旂郴鏂瑰紡", "鑱旂郴", "鐢佃瘽", "寰俊", "绉佷俊",
            "鍜ㄨ", "浜嗚В", "鎯充拱", "鎯宠", "闇€瑕?, "姹傛帹鑽?, "鎺ㄨ崘", "鍙互鍚?, "鑳藉悧", "閫傚悎",
        )
        meaningful = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", "") or "").strip()
            compact_content = "".join(content.split())
            if not compact_content:
                continue
            if any(word in compact_content for word in abusive_keywords):
                continue
            if compact_content in trivial_exact:
                continue
            if compact_content in off_topic_chat_exact:
                continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", compact_content):
                continue
            text_only = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", compact_content)
            if len(text_only) <= 1:
                continue
            has_intent_signal = any(word in compact_content for word in intent_keywords)
            if not has_intent_signal and not self._is_topic_related_comment(compact_content, post_title):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            meaningful.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": "鍙嶅悜绛涢€夛細璇勮鏈夊疄闄呭唴瀹癸紝涓斾笌褰撳墠瑙嗛涓婚鐩稿叧锛屼笉灞炰簬鏃犳剰涔?鏀诲嚮/鐏屾按",
                "intent_level": "high",
                "score": 0.58,
            })
            seen.add(key)
        return meaningful

    def filter_with_prompt(self, user_prompt: str) -> str:
        """鐩存帴浣跨敤prompt杩涜绛涢€夛紝杩斿洖AI鐨勫師濮嬪洖澶?""
        try:
            response = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "浣犳槸涓€涓笓涓氱殑灏忕孩涔﹀唴瀹瑰垎鏋愬笀锛屾搮闀跨瓫閫夐珮璐ㄩ噺鍐呭銆?},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 2000
                },
                headers=self.headers,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                print(f"API閿欒: {response.status_code}, body={response.text[:500]}")
                return "{}"
        except Exception as e:
            print(f"璋冪敤AI澶辫触: {str(e)}")
            return "{}"

    def generate_comment(self, username: str, post_title: str, direction: str = "浜插垏銆佹湁瓒?) -> str:
        """
        鐢ˋI鐢熸垚璇勮鍐呭

        Args:
            username: 瑕佽瘎璁虹殑鐢ㄦ埛鍚?            post_title: 甯栧瓙鏍囬
            direction: 璇勮椋庢牸鏂瑰悜

        Returns:
            鐢熸垚鐨勮瘎璁哄唴瀹?        """
        system_prompt = f"""浣犳槸涓€涓儹鎯呭弸濂藉皬绾功鐢ㄦ埛銆傝鏍规嵁浠ヤ笅淇℃伅鐢熸垚涓€鏉¤瘎璁恒€?
瑕佹眰锛?1. 璇皵锛歿direction}
2. 鑷劧鐪熷疄锛屽儚鐪熶汉璇勮
3. 涓嶈秴杩?0瀛?4. 涓嶈澶畼鏂规垨钀ラ攢鎰?5. 鍙互閫傚綋鎻愰棶鎴栬〃杈惧叡楦?""

        user_prompt = f"""甯栧瓙鏍囬: {post_title}
瑕佽瘎璁虹殑鐢ㄦ埛: @{username}

璇风敓鎴愪竴鏉￠€傚悎鐨勮瘎璁恒€?""

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 200
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip()
            else:
                return "鍐欏緱鐪熷ソ锛屾敮鎸佷竴涓嬶紒"

        except Exception as e:
            print(f"鐢熸垚璇勮澶辫触: {str(e)}")
            return "鍐欏緱鐪熷ソ锛屾敮鎸佷竴涓嬶紒"

    @staticmethod
    def _clean_short_reply(text: str, limit: int = 32) -> str:
        value = " ".join(str(text or "").replace("\n", " ").replace("\r", " ").split()).strip()
        value = value.strip("\"'鈥溾€濃€樷€?)
        if len(value) > limit:
            value = value[:limit].rstrip("锛屻€傦紒锛?.!? ")
        return value

    def generate_collection_reply(
        self,
        post_title: str,
        comment_content: str,
        direction: str = "",
    ) -> str:
        system_prompt = (
            "浣犳槸涓€涓湡瀹炵殑灏忕孩涔︾敤鎴凤紝姝ｅ湪鍥炲鍒汉瀵瑰笘瀛愮殑涓€鏉′竴绾ц瘎璁恒€俓n"
            "瑕佹眰锛歕n"
            "1. 鍥炲闈炲父鐭紝12鍒?8涓腑鏂囧瓧绗︼紝鏈€澶?2瀛梊n"
            "2. 鑷劧鍙ｈ锛屼笉瑕佸儚瀹㈡湇锛屼笉瑕佸儚钀ラ攢鍙穃n"
            "3. 瑕佹闈㈠洖搴斿鏂硅瘎璁猴紝鍙互杞诲井甯︿竴鐐圭粡鍘嗘劅銆佹晠浜嬫劅鎴栬鐐筡n"
            "4. 涓嶈兘鐩存帴寮曟祦锛屼笉鑳藉嚭鐜颁富椤点€佺淇°€佸姞鎴戙€佸挩璇€佽绋嬨€佹姤浠枫€侀摼鎺ョ瓑璇峔n"
            "5. 鐩爣鏄瀵规柟瑙夊緱浣犳湁涓滆タ锛屾効鎰忛『鎵嬬偣涓婚〉锛屼絾涓嶈兘鏄庤\n"
            "6. 鍙繑鍥炴渶缁堝洖澶嶆枃鏈紝涓嶈瑙ｉ噴"
        )
        user_prompt = (
            f"甯栧瓙涓婚锛歿post_title}\n"
            f"瀵规柟璇勮锛歿comment_content}\n"
            f"椋庢牸鍙傝€冿細{direction or '鑷劧銆佸厠鍒躲€佹湁鐐圭粡鍘嗘劅'}\n"
            "璇疯緭鍑轰竴鏉″彲鐩存帴鍙戦€佺殑鐭洖澶嶃€?
        )
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 120
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                cleaned = self._clean_short_reply(content)
                if cleaned:
                    return cleaned
            else:
                print(f"AI鐢熸垚閲囬泦鍥炲澶辫触: {response.status_code}, body={response.text[:300]}")
        except Exception as e:
            print(f"AI鐢熸垚閲囬泦鍥炲澶辫触: {str(e)}")

        fallback = "鎴戜篃鏄瘯浜嗗嚑鐗堬紝鍚庨潰鎵嶉『鎵? if any(token in str(comment_content or "") for token in ["?", "锛?, "鍚?, "涔?]) else "鎴戝綋鏃朵篃韪╄繃鍧戯紝鍚庨潰鎵嶆參鎱㈤『"
        return self._clean_short_reply(fallback)

    def test_connection(self) -> bool:
        """娴嬭瘯API杩炴帴"""
        try:
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "user", "content": "浣犲ソ"}
                ],
                "max_tokens": 50
            }

            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=10
            )

            return response.status_code == 200

        except Exception as e:
            print(f"API杩炴帴娴嬭瘯澶辫触: {str(e)}")
            return False
