import json
import re
import sys
import requests
import copy
from io import StringIO
from ruamel.yaml import YAML
from typing import List, Dict, Any, Tuple
import os

# --- 配置 ---
LLM_SERVICE_URL = os.environ.get("LLM_SERVICE_URL", "http://192.168.5.19:31857/translate")

# --- 内容分类器 ---
MIXED_CONTENT_PATTERN = re.compile(
    r'```'              # 多行代码块
    r'|<[^>]+>'         # HTML/MDX 标签
    r'|`[^`]+`'         # 行内代码
    r'|^#+\s'           # Markdown 标题
    r'|\{[a-zA-Z_][a-zA-Z0-9_]*\}' # 变量占位符
)

def is_mixed_content(text: str) -> bool:
    return bool(MIXED_CONTENT_PATTERN.search(text))

# ==============================================================================
# JSON 文件专用处理逻辑 (无变动)
# ==============================================================================

def find_strings_in_json_recursive(data: Any, path: str, strings_to_translate: List[str], paths: List[str]):
    if isinstance(data, dict):
        for key, value in data.items(): find_strings_in_json_recursive(value, f"{path}.{key}", strings_to_translate, paths)
    elif isinstance(data, list):
        for index, item in enumerate(data): find_strings_in_json_recursive(item, f"{path}[{index}]", strings_to_translate, paths)
    elif isinstance(data, str) and data.strip():
        if re.search(r'[\u4e00-\u9fa5]', data):
            strings_to_translate.append(data); paths.append(path)

def place_strings_in_json_recursive(data: Any, path_segments: List[str], value: str):
    if not path_segments: return
    current_segment, remaining_segments = path_segments[0], path_segments[1:]
    match = re.match(r'(.+)\[(\d+)\]', current_segment)
    if match:
        key, index = match.groups(); index = int(index)
        if not remaining_segments: data[key][index] = value
        else: place_strings_in_json_recursive(data[key][index], remaining_segments, value)
    else:
        if not remaining_segments: data[current_segment] = value
        else: place_strings_in_json_recursive(data[current_segment], remaining_segments, value)

def handle_json_translation(
    file_path: str, source_language:str, source_language_full: str, target_languages: List[str], target_language_full: List[str]
) -> Dict[str, str]:
    with open(file_path, "r", encoding="utf-8") as f: original_data = json.load(f)
    strings_to_translate, paths = [], []; find_strings_in_json_recursive(original_data, "", strings_to_translate, paths)
    paths = [p.lstrip('.') for p in paths]
    if not strings_to_translate: return {k: json.dumps(original_data, ensure_ascii=False, indent=2) for k in target_languages}
    print(f" G- 正在发送 {len(strings_to_translate)} 个 JSON 字符串片段到翻译服务...")
    payload = {"texts_to_translate": strings_to_translate, "source_language_full": source_language_full, "target_languages": target_languages, "target_language_full": target_language_full}
    try:
        response = requests.post(LLM_SERVICE_URL, json=payload, timeout=300); response.raise_for_status()
        translated_segments_by_lang = response.json()['translations']
    except requests.exceptions.RequestException as e: print(f"❌ 错误: 无法连接到翻译服务。错误: {e}"); sys.exit(1)
    final_json_outputs = {}
    for lang in target_languages:
        translated_strings = translated_segments_by_lang[lang]
        data_copy = copy.deepcopy(original_data)
        for i, path_str in enumerate(paths):
            place_strings_in_json_recursive(data_copy, path_str.split('.'), translated_strings[i])
        final_json_outputs[lang] = json.dumps(data_copy, ensure_ascii=False, indent=2)
    return final_json_outputs

# ==============================================================================
# MDX 和其他文本文件的处理逻辑
# ==============================================================================

# <<< 核心修复点在这里！>>>
def extract_chinese_and_create_template(text: str) -> Tuple[List[str], str]:
    """
    从混合文本中提取待翻译的中文片段，并创建一个带占位符的模板。
    关键修复：正则表达式现在只匹配中文字符和中文标点，不再错误地包含空白字符(\s)或数字(\d)。
    """
    pattern = r'([\u4e00-\u9fa5][\u4e00-\u9fa5，。？！、；：“”‘’（）《》〈〉【】…—～]*)'
    parts = re.split(pattern, text)
    chinese_segments, template_parts = [], []
    placeholder_idx = 0
    for part in parts:
        if not part: continue
        if re.fullmatch(pattern, part):
            chinese_segments.append(part)
            template_parts.append(f"{{{placeholder_idx}}}")
            placeholder_idx += 1
        else:
            escaped_part = part.replace('{', '{{').replace('}', '}}')
            template_parts.append(escaped_part)
            
    if not chinese_segments: return [], text
    return chinese_segments, "".join(template_parts)

def parse_content(content):
    pattern = r'^(?P<pre_meta>(?:<!--[\s\S]*?-->\s*)*)?---\s*\n(?P<yaml_content>[\s\S]*?)\n---(?P<body_content>[\s\S]*)'
    match = re.match(pattern, content, re.DOTALL)
    if not match: return "", [], [], content
    pre_meta_content = match.group('pre_meta') or ""
    yaml_content = match.group('yaml_content').strip()
    body_content = match.group('body_content')
    yaml = YAML(); metadata_dict = yaml.load(yaml_content)
    return pre_meta_content, list(metadata_dict.keys()), [str(v) for v in metadata_dict.values()], body_content

def parse_body_segments(body_content):
    pattern = r'(<!-- l10n-id: [\s\S]*? -->)'
    parts = re.split(pattern, body_content)
    segments = []
    for part in parts:
        if not part: continue
        if re.match(pattern, part): segments.append({'type': 'id', 'content': part})
        elif part.strip(): segments.append({'type': 'translatable_text', 'content': part})
        else: segments.append({'type': 'whitespace', 'content': part})
    return segments

def reconstruct_mdx_file(pre_meta_content: str, original_keys: list, translated_meta_values: list, original_body_segments: list, reconstructed_body_texts: list):
    if original_keys:
        new_metadata = dict(zip(original_keys, translated_meta_values))
        yaml = YAML(); string_stream = StringIO()
        yaml.dump(new_metadata, string_stream)
        frontmatter = f"---\n{string_stream.getvalue()}---\n\n"
    else: frontmatter = ""
    body_parts = []; translated_text_idx = 0
    for segment in original_body_segments:
        if segment['type'] == 'translatable_text':
            if translated_text_idx < len(reconstructed_body_texts):
                body_parts.append(reconstructed_body_texts[translated_text_idx])
                translated_text_idx += 1
            else: body_parts.append(segment['content'])
        else: body_parts.append(segment['content'])
    reconstructed_body = "".join(body_parts)
    return pre_meta_content + frontmatter + reconstructed_body

# ==============================================================================
# 主翻译流程
# ==============================================================================

def translate_file(file_path: str, source_language:str, source_language_full: str, target_languages: list[str], target_language_full: list[str]):
    if len(target_languages) != len(target_language_full): raise ValueError("target_languages and target_language_full must have the same length.")
    # 这里我修正了您的测试代码，使其能根据文件名动态显示正确的日志
    file_type = "JSON" if file_path.endswith('.json') else "MDX/文本"
    print(f"\n==================== 正在翻译 {file_type} 文件 ====================")
    print(f" G- 文件路径: {file_path}")

    if file_path.endswith('.json'):
        ans = handle_json_translation(file_path, source_language, source_language_full, target_languages, target_language_full)
        print(ans)
    else:
        ans = handle_mdx_translation(file_path, source_language, source_language_full, target_languages, target_language_full)
    for lang in target_languages:
        ans[lang] = ans[lang].replace(f'/{source_language}/', f'/{lang}/')
    return ans
        

def handle_mdx_translation(file_path: str, source_language:str, source_language_full: str, target_languages: list[str], target_language_full: list[str]):
    with open(file_path, "r", encoding="utf-8") as f: original_content = f.read()

    pre_meta, meta_keys, meta_values, raw_body = parse_content(original_content)
    body_segments = parse_body_segments(raw_body)
    body_texts_to_process = [seg['content'] for seg in body_segments if seg['type'] == 'translatable_text']
    all_texts_to_process = meta_values + body_texts_to_process

    texts_for_api, reconstruction_plan = [], []
    print(" G- 正在智能分析文本，区分纯文本与混合内容...")
    for text in all_texts_to_process:
        if not re.search(r'[\u4e00-\u9fa5]', text) and source_language == 'zh':
            reconstruction_plan.append({'type': 'verbatim', 'content': text})
            continue
        if is_mixed_content(text):
            chinese_parts, template = extract_chinese_and_create_template(text)
            texts_for_api.extend(chinese_parts)
            reconstruction_plan.append({'type': 'mixed', 'template': template, 'count': len(chinese_parts)})
        else:
            texts_for_api.append(text)
            reconstruction_plan.append({'type': 'pure_text', 'count': 1})

    if not texts_for_api: print("ℹ️ 未找到需要翻译的中文内容。"); return { k: original_content for k in target_languages }

    print(f" G- 正在发送 {len(texts_for_api)} 个文本单元到翻译服务...")
    payload = {"texts_to_translate": texts_for_api, "source_language_full": source_language_full, "target_languages": target_languages, "target_language_full": target_language_full}
    try:
        response = requests.post(LLM_SERVICE_URL, json=payload, timeout=300); response.raise_for_status()
        translated_units_by_lang = response.json()['translations']
    except requests.exceptions.RequestException as e: print(f"❌ 错误: 无法连接到翻译服务。错误: {e}"); sys.exit(1)

    print(" G- 正在根据翻译计划重建文件...")
    final_reconstructed_content = {}
    for lang in target_languages:
        translated_units = translated_units_by_lang[lang]
        reconstructed_texts, unit_cursor = [], 0
        for plan_item in reconstruction_plan:
            plan_type = plan_item['type']
            if plan_type == 'verbatim': reconstructed_texts.append(plan_item['content'])
            elif plan_type == 'pure_text':
                reconstructed_texts.append(translated_units[unit_cursor]); unit_cursor += 1
            elif plan_type == 'mixed':
                count, template = plan_item['count'], plan_item['template']
                if count == 0: reconstructed_texts.append(template)
                else:
                    current_units = translated_units[unit_cursor : unit_cursor + count]
                    reconstructed_texts.append(template.format(*current_units)); unit_cursor += count
        
        reconstructed_meta, reconstructed_body = reconstructed_texts[:len(meta_values)], reconstructed_texts[len(meta_values):]
        final_reconstructed_content[lang] = reconstruct_mdx_file(pre_meta, meta_keys, reconstructed_meta, body_segments, reconstructed_body)
        final_reconstructed_content[lang] = final_reconstructed_content[lang].replace('{{', '{').replace('}}', '}')
    return final_reconstructed_content


if __name__ == "__main__":
    try:
        print("\n" + "="*20 + " 正在翻译 JSON 文件 " + "="*20)
        ans_json = translate_file(
            # '../../content/docs/user/meta.json',
            '../../content/docs/user/index.mdx',
            'zh',
            '简体中文',
            ['en', 'ja'],
            ['English', '日本語']
        )
        for lang, content in ans_json.items():
            print(f"--- {lang} (meta.json) ---")
            print(content)
            print()
    except Exception as e:
        print(f"\n❌ 客户端执行时发生未处理的错误: {e}")
        import traceback
        traceback.print_exc()