# -*- coding: utf-8 -*-
# 功能:
# 1. 引导框架负责自动扫描、文件I/O和多语言管理。
# 2. 核心翻译逻辑完全由 translation_client.py 模块驱动。
# 3. 动态读取原生i18n配置。
# 4. 统一处理.md .mdx 和 meta.json 文件的翻译流程。

import os
import re
import sys
import json
import argparse
from typing import Dict, List, Tuple
from pathlib import Path

try:
    from translation_client import translate_files
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
SCAN_DIRECTORIES = [
    PROJECT_ROOT / 'content' / 'docs',
    PROJECT_ROOT / 'messages'
]
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

def main(args):
    """主执行函数，根据命令行参数执行不同策略。"""
    print("\n🚀 欢迎使用i18n自动化引导程序！")
    
    default_locale, locales_map = get_i18n_config()
    supported_locales = list(locales_map.keys())

    # --- 步骤 1: 扫描所有指定目录，建立文档家族 ---
    doc_families: Dict[str, Dict[str, Path]] = {}
    print("\n🔍 正在扫描以下目录:")
    for directory in SCAN_DIRECTORIES:
        print(f"  - {directory.relative_to(PROJECT_ROOT)}")
        for root, _, files in os.walk(directory):
            for file in files:
                if not (file.endswith('.mdx') or file.endswith('.json') or file.endswith('.md')):
                    continue
                
                file_path = Path(root) / file
                # 假设 get_path_prefix_and_lang 能正确处理路径
                path_prefix, lang = get_path_prefix_and_lang(str(file_path), default_locale, supported_locales)
                
                if path_prefix not in doc_families:
                    doc_families[path_prefix] = {}
                doc_families[path_prefix][lang] = file_path
            
    print(f"📊 扫描完成，共找到 {len(doc_families)} 个文档家族。")

    # --- 步骤 2: 根据变更文件列表过滤受影响的家族 (逻辑不变) ---
    changed_files_list = []
    if args.changed_files:
        print(f"\n🔄 检测到变更文件列表，将只处理受影响的文档家族。")
        # 将逗号分隔的字符串转换为 Path 对象列表
        changed_files_list = [PROJECT_ROOT / p.strip() for p in args.changed_files.split(',') if p.strip()]
        
        affected_families = {}
        for prefix, files_map in doc_families.items():
            # 检查该家族中是否有任何文件在变更列表中
            if any(path in changed_files_list for path in files_map.values()):
                affected_families[prefix] = files_map
        
        doc_families = affected_families
        if not doc_families:
            print("✅ 所有变更的文件都不属于任何已知文档家族，本次无需翻译。")
            sys.exit(0)
        print(f"  - 共 {len(doc_families)} 个文档家族受到影响。")

    # --- 步骤 3: 遍历受影响的家族，智能执行翻译 ---
    for prefix, files_map in doc_families.items():
        # (这部分用于打印相对路径，可以保持不变)
        relative_prefix_str = prefix.replace(str(PROJECT_ROOT), '').lstrip('/')
        print(f"\n➡️ 正在处理文档家族: '{relative_prefix_str}'")

        # --- 3.1 智能确定本次操作的“真相来源 (Source of Truth)” ---
        source_of_truth_lang = None
        source_of_truth_path = None

        # 找出这个家族里哪些文件被修改了
        family_changed_files = {lang: path for lang, path in files_map.items() if path in changed_files_list}

        if default_locale in family_changed_files:
            # 优先规则：如果默认语言文件被修改，它就是源头
            source_of_truth_lang = default_locale
            source_of_truth_path = family_changed_files[default_locale]
            print(f"  - 策略：检测到默认语言 '{default_locale}' 文件被修改，将以它为基准。")
        elif len(family_changed_files) == 1:
            # 次要规则：如果只有一个非默认语言文件被修改
            source_of_truth_lang, source_of_truth_path = list(family_changed_files.items())[0]
            print(f"  - 策略：检测到只有 '{source_of_truth_lang}' 文件被修改，将以它为基准。")
        elif len(family_changed_files) > 1:
            # 冲突规则：修改了多个非默认语言文件，意图不明
            print(f"  - ❌ 错误：检测到同家族内有多个非默认语言文件被修改 ({list(family_changed_files.keys())})。无法确定翻译基准，跳过此家族。")
            continue
        else:
            # Fallback 规则：没有文件被修改（例如，全局添加新语言），或变更的文件是新增的
            if default_locale in files_map:
                source_of_truth_lang = default_locale
                source_of_truth_path = files_map[default_locale]
                print(f"  - 策略：未检测到文件变更，使用默认语言 '{default_locale}' 为基准。")
            elif files_map:
                source_of_truth_lang, source_of_truth_path = list(files_map.items())[0]
                print(f"  - 策略：未检测到文件变更且默认语言文件不存在，使用找到的第一个语言 '{source_of_truth_lang}' 为基准。")
            else:
                # 这种情况理论上不会发生，因为家族不为空
                print(f"  - ❌ 错误：文档家族为空，无法确定源文件。")
                continue
        
        print(f"  - 基准文件: '{source_of_truth_path.relative_to(PROJECT_ROOT)}'")

        # --- 3.2 识别需要创建和需要更新的目标 ---
        targets_to_create = [lang for lang in supported_locales if lang not in files_map]
        targets_to_update = [lang for lang in supported_locales if lang in files_map and lang != source_of_truth_lang]

        # --- 3.3 执行翻译 ---
        # (A) 创建缺失的语言文件
        if targets_to_create:
            print(f"  - 任务：准备为以下缺失语言创建新文件: {targets_to_create}")
            # 对于创建，我们只提供源文件，让 client 生成新内容
            creation_results = translate_files(
                file_paths=[str(source_of_truth_path)],
                source_language=source_of_truth_lang,
                source_language_full=locales_map[source_of_truth_lang],
                target_languages=targets_to_create,
                target_language_full=[locales_map[lang] for lang in targets_to_create],
                write_to_existing_files=False # 确保这是 False
            )
            # 写入新创建的文件
            for lang, content in creation_results.items():
                source_suffix = source_of_truth_path.suffix
                target_path: Path

                # === 核心修正逻辑 ===
                # 检查源文件的文件名（不含后缀）本身是否就是一个支持的语言代码 (e.g., 'zh' from 'zh.json')
                # 这能准确地区分 'zh.json' 和 'index.mdx' 这类文件。
                if source_of_truth_path.stem in supported_locales:
                    # 对于 'zh.json' 这种情况, prefix 是它所在的目录, e.g., '.../messages'
                    # 正确的目标路径是 目录 / 新语言代码.后缀, e.g., '.../messages/ko.json'
                    target_path = Path(prefix) / f"{lang}{source_suffix}"
                else:
                    # 对于 'index.mdx' 或 'index.zh.mdx' 这类文件，使用原始逻辑
                    # prefix 是 '.../index'
                    # 正确的路径是 前缀.新语言代码.后缀
                    # 例如: '.../index.ko.mdx'
                    if lang == default_locale:
                        # 默认语言不需要语言代码后缀
                        target_path = Path(f"{prefix}{source_suffix}")
                    else:
                        target_path = Path(f"{prefix}.{lang}{source_suffix}")
                
                # 创建可能不存在的父目录
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(target_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"    - ✅ 已创建: '{target_path.relative_to(PROJECT_ROOT)}'")
        else:
            print("  - 任务：无需创建新语言文件。")

        # (B) 更新已有的语言文件
        if targets_to_update:
            print(f"  - 任务：准备更新以下现有文件: {targets_to_update}")
            # 对于更新，我们提供源文件和所有目标文件的路径
            paths_for_update = [str(source_of_truth_path)] + [str(files_map[lang]) for lang in targets_to_update]
            translate_files(
                file_paths=paths_for_update,
                source_language=source_of_truth_lang,
                source_language_full=locales_map[source_of_truth_lang],
                target_languages=targets_to_update,
                target_language_full=[locales_map[lang] for lang in targets_to_update],
                write_to_existing_files=True # 直接让 client 写入
            )
            print(f"    - ✅ 更新任务已提交给翻译客户端。")
        else:
            print("  - 任务：无需更新现有语言文件。")

    print("\n🎉🎉🎉 引导过程全部完成！🎉🎉🎉")

if __name__ == "__main__":
    # 命令行参数解析部分保持不变
    parser = argparse.ArgumentParser(description="i18n 自动化翻译引导程序 (健壮版)")
    parser.add_argument(
        "--changed-files", 
        type=str, 
        default="",
        help="一个用逗号分隔的变更文件路径列表。如果提供，脚本将只处理包含这些文件的文档家族。"
    )
    
    parsed_args = parser.parse_args()
    main(parsed_args)