# scripts/bootstrap.py (V-Final-Refactored)
# 功能:
# 1. 引导框架负责自动扫描、文件I/O和多语言管理。
# 2. 核心翻译逻辑完全由 translation_utils.py 模块驱动。
# 3. 动态读取原生i18n配置。
# 4. 仅为源MDX文件注入段落ID（无div包装）。
# 5. 统一处理 .mdx 和 meta.json 文件的翻译流程。

import os
import re
import sys
import json
import hashlib
from typing import Dict, List, Tuple
from pathlib import Path

try:
    from translation_client import translate_file
except ImportError as e:
    print(f"错误：缺少必要的库 ({e})。")
    sys.exit(1)

# ==============================================================================
# 动态路径配置区
# ==============================================================================
try:
    SCRIPT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = SCRIPT_DIR.parent.parent
except NameError:
    # 兼容在 notebook 等环境中运行
    SCRIPT_DIR = Path.cwd()
    PROJECT_ROOT = SCRIPT_DIR

# ==============================================================================
# 启动前的配置区
# ==============================================================================
DOCS_ROOT_DIR = PROJECT_ROOT / 'content'
DOCS_ROOT_DIR_2 = PROJECT_ROOT / 'messages'
I18N_CONFIG_PATH = PROJECT_ROOT / 'src' / 'i18n' / 'config.ts'

# ==============================================================================
# 引导框架
# ==============================================================================

def get_i18n_config() -> Tuple[str, Dict[str, str]]:
    """从 Starlight 配置文件中读取默认语言和支持的语言列表。"""
    print(f"🤖 正在从 '{I18N_CONFIG_PATH}' 读取原生i18n配置...")
    try:
        with open(I18N_CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ 错误：配置文件 '{I18N_CONFIG_PATH}' 未找到！请检查路径。")
        sys.exit(1)
        
    default_locale_match = re.search(r"defaultLocale:.*=\s*['\"](\w+)['\"]", content)
    if not default_locale_match:
        print(f"❌ 错误：无法在 '{I18N_CONFIG_PATH}' 中找到 'defaultLocale'。")
        sys.exit(1)
    default_locale = default_locale_match.group(1)

    locales_match = re.search(r"supportedLocales\s*=\s*{(.*?)}", content, re.DOTALL)
    if not locales_match:
        print(f"❌ 错误：无法在 '{I18N_CONFIG_PATH}' 中找到 'supportedLocales'。")
        sys.exit(1)
    supported_locales_str = locales_match.group(1)
    
    locale_pairs = re.findall(r"(\w+):\s*['\"](.*?)['\"]", supported_locales_str)
    locales_map = {key.strip(): value.strip() for key, value in locale_pairs}
    
    print(f"✅ 配置读取成功: 默认语言='{default_locale}', 支持的语言={list(locales_map.keys())}")
    return default_locale, locales_map


def get_path_prefix_and_lang(file_path_str: str, default_locale: str, supported_locales: List[str]) -> Tuple[str, str]:
    """解析文件路径，返回其语言无关的前缀和语言代码。"""
    file_path = Path(file_path_str)
    dir_path = file_path.parent
    base_name = file_path.stem

    # 模式 1: 检查 'name.lang' 格式, e.g., 'index.en' from 'index.en.mdx'
    base_name_parts = base_name.split('.')
    if len(base_name_parts) > 1 and base_name_parts[-1] in supported_locales:
        lang = base_name_parts[-1]
        path_prefix = str(dir_path / ".".join(base_name_parts[:-1]))
        return path_prefix, lang

    # 模式 2: 检查 'lang' 格式, e.g., 'en' from 'en.json'
    if base_name in supported_locales:
        lang = base_name
        # 对于这种模式，文档家族的前缀就是它所在的目录
        path_prefix = str(dir_path)
        return path_prefix, lang

    # 模式 3: 默认语言文件, e.g., 'index' from 'index.mdx'
    lang = default_locale
    path_prefix = str(dir_path / base_name)
    return path_prefix, lang

def main():
    """主执行函数，调用外部翻译模块。"""
    print("\n🚀 欢迎使用i18n自动化引导程序！(重构版)")
    
    default_locale, locales_map = get_i18n_config()
    supported_locales = list(locales_map.keys())

    print(f"\n🔍 正在扫描文档目录 '{DOCS_ROOT_DIR}'...")
    
    doc_families: Dict[str, Dict[str, Path]] = {}
    for root, _, files in os.walk(DOCS_ROOT_DIR):
        for file in files:
            if not (file.endswith('.mdx') or file.endswith('.json')):
                continue
            
            file_path = Path(root) / file
            path_prefix, lang = get_path_prefix_and_lang(str(file_path), default_locale, supported_locales)
            
            if path_prefix not in doc_families:
                doc_families[path_prefix] = {}
            doc_families[path_prefix][lang] = file_path

    print(f"🔍 正在扫描文档目录 '{DOCS_ROOT_DIR_2}'...")
    for root, _, files in os.walk(DOCS_ROOT_DIR_2):
        for file in files:
            if not (file.endswith('.mdx') or file.endswith('.json')):
                continue
            
            file_path = Path(root) / file
            path_prefix, lang = get_path_prefix_and_lang(str(file_path), default_locale, supported_locales)
            print(f" 发现文件: '{file_path.relative_to(PROJECT_ROOT)}' -> 前缀: '{path_prefix}', 语言: '{lang}'")
            
            if path_prefix not in doc_families:
                doc_families[path_prefix] = {}
            doc_families[path_prefix][lang] = file_path
            
    print(f"📊 扫描完成，共找到 {len(doc_families)} 个文档家族。")

    for prefix, files_map in doc_families.items():
        relative_prefix_str = ""
        try:
            # 优先尝试相对于主内容目录
            relative_prefix_str = Path(prefix).relative_to(DOCS_ROOT_DIR)
        except ValueError:
            try:
                # 如果失败，再尝试相对于次内容目录
                relative_prefix_str = Path(prefix).relative_to(DOCS_ROOT_DIR_2)
            except ValueError:
                # 如果都失败，直接使用前缀（作为降级策略）
                relative_prefix_str = prefix

        print(f"\n➡️ 正在处理文档家族: '{relative_prefix_str}'")
        
        source_lang, source_file_path = "", Path()
        if default_locale in files_map:
            source_lang = default_locale
            source_file_path = files_map[default_locale]
        else:
            if not files_map: continue
            # 如果默认语言文件不存在，则选择找到的第一个作为源文件
            source_lang, source_file_path = next(iter(files_map.items()))
        
        print(f"  - 源文件确定为: '{source_file_path.relative_to(PROJECT_ROOT)}' (语言: {source_lang})")

        # 步骤 2: 确定需要翻译的目标语言
        target_langs_to_process = [lang for lang in supported_locales if lang not in files_map]
        if not target_langs_to_process:
            print("  - 所有语言版本已存在，无需翻译。")
            continue

        print(f"  - 正在将源文件翻译到: {target_langs_to_process}...")
        
        # 步骤 3: 调用统一的翻译函数
        translated_contents = translate_file(
            file_path=str(source_file_path),
            source_language=source_lang,
            source_language_full=locales_map[source_lang],
            target_languages=target_langs_to_process,
            target_language_full=[locales_map[lang] for lang in target_langs_to_process],
        )
        
        # 步骤 4: 写入翻译后的文件
        source_suffix = source_file_path.suffix
        for lang, content in translated_contents.items():
            # 构建目标路径，例如 /path/to/index.en.mdx 或 /path/to/meta.en.json
            target_path = Path(f"{prefix}.{lang}{source_suffix}")
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  - ✅ 已创建翻译文件: '{target_path.relative_to(PROJECT_ROOT)}'")

    print("\n🎉🎉🎉 引导过程全部完成！🎉🎉🎉")

if __name__ == "__main__":
    main()