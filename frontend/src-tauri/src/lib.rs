use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::Mutex;

use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use rand::Rng;
use serde::{Deserialize, Serialize};
use tauri::{Emitter, Manager, State};
use tauri_plugin_shell::ShellExt;

// ── Sidecar State ─────────────────────────────────

struct SidecarState {
  port: Option<u16>,
  child: Option<tauri_plugin_shell::process::CommandChild>,
  starting: bool,
}

#[tauri::command]
async fn sidecar_start(
  state: State<'_, Mutex<SidecarState>>,
  app: tauri::AppHandle,
) -> Result<u16, String> {
  // Check if already running or starting
  {
    let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
    if let Some(port) = s.port {
      return Ok(port);
    }
    if s.starting {
      return Err("Sidecar is already starting".to_string());
    }
    s.starting = true;
  }

  // Pick a random port in ephemeral range
  let port: u16 = rand::thread_rng().gen_range(49152..=65535);

  // Spawn the sidecar binary
  let (mut rx, child) = app
    .shell()
    .sidecar("ai-reader-sidecar")
    .map_err(|e| format!("Failed to create sidecar command: {e}"))?
    .args([
      "--port",
      &port.to_string(),
    ])
    .spawn()
    .map_err(|e| format!("Failed to spawn sidecar: {e}"))?;

  // Read stdout for PORT:xxxxx line (with async timeout)
  let (tx_port, rx_port) = tokio::sync::oneshot::channel::<u16>();
  let tx_port = std::sync::Mutex::new(Some(tx_port));
  let expected_port = port;

  // Spawn async task to read sidecar output
  tauri::async_runtime::spawn(async move {
    while let Some(event) = rx.recv().await {
      match event {
        tauri_plugin_shell::process::CommandEvent::Stdout(line) => {
          let line_str = String::from_utf8_lossy(&line);
          for l in line_str.lines() {
            if let Some(p) = l.strip_prefix("PORT:") {
              if let Ok(parsed) = p.trim().parse::<u16>() {
                if let Some(tx) = tx_port.lock().unwrap().take() {
                  let _ = tx.send(parsed);
                }
              }
            }
          }
          log::info!("[sidecar] {}", line_str.trim());
        }
        tauri_plugin_shell::process::CommandEvent::Stderr(line) => {
          log::info!("[sidecar:err] {}", String::from_utf8_lossy(&line).trim());
        }
        _ => {}
      }
    }
  });

  // Wait for PORT line asynchronously (up to 120s — PyInstaller cold start extracts 218MB binary;
  // Windows is slower due to antivirus scanning during extraction)
  let reported_port = match tokio::time::timeout(
    std::time::Duration::from_secs(120),
    rx_port,
  ).await {
    Ok(Ok(p)) => p,
    Ok(Err(_)) => {
      let _ = child.kill();
      let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
      s.starting = false;
      return Err("Sidecar stdout closed before reporting port".to_string());
    }
    Err(_) => {
      let _ = child.kill();
      let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
      s.starting = false;
      return Err("Sidecar did not report port within 60s".to_string());
    }
  };

  if reported_port != expected_port {
    log::warn!(
      "Sidecar reported port {} but expected {}; using reported",
      reported_port,
      expected_port
    );
  }

  // Health check: poll GET /api/health up to 120 times (1s apart, ~120s max)
  // PyInstaller single-file binary needs ~50-60s on cold start (extract + scipy/numpy import)
  let health_url = format!("http://127.0.0.1:{}/api/health", reported_port);
  let client = reqwest::Client::new();
  let mut healthy = false;
  for _ in 0..120 {
    tokio::time::sleep(std::time::Duration::from_millis(1000)).await;
    match client.get(&health_url).send().await {
      Ok(resp) if resp.status().is_success() => {
        healthy = true;
        break;
      }
      _ => {}
    }
  }

  if !healthy {
    // Kill the child if health check fails
    let _ = child.kill();
    let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
    s.starting = false;
    return Err("Sidecar failed health check after 120s".to_string());
  }

  // Store state
  {
    let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
    s.port = Some(reported_port);
    s.child = Some(child);
    s.starting = false;
  }

  log::info!("Sidecar started on port {}", reported_port);
  Ok(reported_port)
}

#[tauri::command]
fn sidecar_stop(state: State<'_, Mutex<SidecarState>>) -> Result<(), String> {
  let mut s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
  if let Some(child) = s.child.take() {
    let _ = child.kill();
    log::info!("Sidecar stopped");
  }
  s.port = None;
  Ok(())
}

#[tauri::command]
fn sidecar_status(state: State<'_, Mutex<SidecarState>>) -> Result<Option<u16>, String> {
  let s = state.lock().map_err(|e| format!("Lock error: {e}"))?;
  Ok(s.port)
}

/// Read a bundled resource file (supports both .json and .json.gz)
/// Returns the decompressed JSON string to the frontend.
#[tauri::command]
fn load_resource(app: tauri::AppHandle, path: String) -> Result<String, String> {
  let resource_path = app
    .path()
    .resolve(&path, tauri::path::BaseDirectory::Resource)
    .map_err(|e| format!("路径解析失败: {e}"))?;

  let bytes = std::fs::read(&resource_path)
    .map_err(|e| format!("读取文件失败: {} — {e}", resource_path.display()))?;

  decompress_if_gzip(bytes)
}

/// Read a file from an absolute path (for user-imported data outside bundle)
#[tauri::command]
fn load_file_absolute(path: String) -> Result<String, String> {
  let bytes = std::fs::read(&path)
    .map_err(|e| format!("读取文件失败: {path} — {e}"))?;

  decompress_if_gzip(bytes)
}

/// Return the user novels directory path (app_data_dir/novels/)
#[tauri::command]
fn get_user_novels_dir(app: tauri::AppHandle) -> Result<String, String> {
  let data_dir = app
    .path()
    .app_data_dir()
    .map_err(|e| format!("获取数据目录失败: {e}"))?;
  let novels_dir = data_dir.join("novels");
  Ok(novels_dir.to_string_lossy().to_string())
}

// ── .air Preview & Import ────────────────────────

#[derive(Serialize)]
struct PreviewResult {
  title: String,
  author: Option<String>,
  total_chapters: usize,
  total_words: u64,
  analyzed_chapters: usize,
  has_precomputed: bool,
  format_version: u32,
  is_duplicate: bool,
  existing_slug: Option<String>,
}

#[derive(Serialize)]
struct ImportResult {
  slug: String,
  title: String,
  total_chapters: usize,
}

/// Quick-parse an .air file and return metadata preview (no data written)
#[tauri::command]
fn preview_air_file(app: tauri::AppHandle, path: String) -> Result<PreviewResult, String> {
  let air_data = read_and_parse_air(&path)?;

  let novel = air_data
    .get("novel")
    .and_then(|v| v.as_object())
    .ok_or("数据解析失败，缺少 novel 字段")?;

  let title = novel
    .get("title")
    .and_then(|v| v.as_str())
    .unwrap_or("未知")
    .to_string();

  let author = novel
    .get("author")
    .and_then(|v| v.as_str())
    .map(|s| s.to_string());

  let chapters = air_data
    .get("chapters")
    .and_then(|v| v.as_array())
    .map(|a| a.len())
    .unwrap_or(0);

  let total_words = novel
    .get("total_words")
    .and_then(|v| v.as_u64())
    .unwrap_or(0);

  let facts = air_data
    .get("chapter_facts")
    .and_then(|v| v.as_array())
    .map(|a| a.len())
    .unwrap_or(0);

  let format_version = air_data
    .get("format_version")
    .and_then(|v| v.as_u64())
    .unwrap_or(0) as u32;

  let has_precomputed = air_data.get("precomputed").is_some();

  // Check for duplicate
  let slug = sanitize_slug(&title);
  let novels_dir = get_novels_dir(&app)?;
  let (is_duplicate, existing_slug) = check_duplicate(&app, &novels_dir, &slug, &title);

  Ok(PreviewResult {
    title,
    author,
    total_chapters: chapters,
    total_words,
    analyzed_chapters: facts,
    has_precomputed,
    format_version,
    is_duplicate,
    existing_slug,
  })
}

/// Import an .air file: extract precomputed data → write .json.gz files → update manifest
#[tauri::command]
fn import_air_file(
  app: tauri::AppHandle,
  path: String,
  overwrite: bool,
) -> Result<ImportResult, String> {
  let air_data = read_and_parse_air(&path)?;

  let format_version = air_data
    .get("format_version")
    .and_then(|v| v.as_u64())
    .unwrap_or(0);

  if ![1, 2, 3, 4].contains(&format_version) {
    return Err("不支持的文件格式".to_string());
  }

  let precomputed = air_data.get("precomputed");
  if precomputed.is_none() {
    return Err(
      "此 .air 文件版本过旧，请使用最新版 AI Reader 重新导出".to_string(),
    );
  }
  let precomputed = precomputed.unwrap();

  let novel = air_data
    .get("novel")
    .and_then(|v| v.as_object())
    .ok_or("数据解析失败，缺少 novel 字段")?;

  let title = novel
    .get("title")
    .and_then(|v| v.as_str())
    .unwrap_or("未知")
    .to_string();

  let author = novel
    .get("author")
    .and_then(|v| v.as_str())
    .map(|s| s.to_string());

  let slug = sanitize_slug(&title);
  let novels_dir = get_novels_dir(&app)?;
  let novel_dir = novels_dir.join(&slug);

  // Duplicate check
  let (is_dup, _) = check_duplicate(&app, &novels_dir, &slug, &title);
  if is_dup && !overwrite {
    return Err(format!("「{title}」已存在，请选择覆盖导入"));
  }

  // Atomic import: write to temp dir first, rename on success
  let tmp_dir = novels_dir.join(format!(".importing-{}", slug));
  // Clean up any stale temp dir from a previous failed import
  let _ = std::fs::remove_dir_all(&tmp_dir);

  let chapters_dir = tmp_dir.join("chapters");
  std::fs::create_dir_all(&chapters_dir)
    .map_err(|e| format!("创建目录失败: {e}"))?;

  // Wrap the file-writing phase so we can clean up on failure
  let import_result = (|| -> Result<(usize, NovelStats), String> {
    // Write precomputed visualization files
    let viz_keys = [
      "graph",
      "map",
      "timeline",
      "encyclopedia",
      "encyclopedia_stats",
      "factions",
      "world_structure",
    ];
    let file_names = [
      "graph.json.gz",
      "map.json.gz",
      "timeline.json.gz",
      "encyclopedia.json.gz",
      "encyclopedia-stats.json.gz",
      "factions.json.gz",
      "world-structure.json.gz",
    ];

    for (key, filename) in viz_keys.iter().zip(file_names.iter()) {
      if let Some(data) = precomputed.get(*key) {
        write_json_gz(&tmp_dir.join(filename), data)?;
      }
    }

    // Write chapter data
    let chapters = air_data
      .get("chapters")
      .and_then(|v| v.as_array());

    let total_chapters = if let Some(chs) = chapters {
      // Write chapters list (metadata without content)
      let chapters_meta: Vec<serde_json::Value> = chs
        .iter()
        .map(|ch| {
          let mut meta = serde_json::Map::new();
          if let Some(obj) = ch.as_object() {
            for (k, v) in obj {
              if k != "content" {
                meta.insert(k.clone(), v.clone());
              }
            }
          }
          serde_json::Value::Object(meta)
        })
        .collect();

      write_json_gz(
        &tmp_dir.join("chapters.json.gz"),
        &serde_json::Value::Array(chapters_meta),
      )?;

      // Write individual chapter files (with content + entities from chapter_facts)
      let facts_map = build_chapter_facts_map(&air_data);

      for ch in chs {
        let chapter_num = ch
          .get("chapter_num")
          .and_then(|v| v.as_u64())
          .unwrap_or(0);

        let mut chapter_data = serde_json::Map::new();
        chapter_data.insert(
          "chapter_num".to_string(),
          serde_json::Value::Number(chapter_num.into()),
        );
        chapter_data.insert(
          "title".to_string(),
          ch.get("title").cloned().unwrap_or(serde_json::Value::String("".to_string())),
        );
        chapter_data.insert(
          "content".to_string(),
          ch.get("content").cloned().unwrap_or(serde_json::Value::String("".to_string())),
        );
        chapter_data.insert(
          "word_count".to_string(),
          ch.get("word_count").cloned().unwrap_or(serde_json::Value::Number(0.into())),
        );

        // Extract entities from chapter_facts for this chapter
        if let Some(fact) = facts_map.get(&chapter_num) {
          let entities = extract_chapter_entities(fact);
          chapter_data.insert("entities".to_string(), entities);
        }

        let padded = format!("ch-{:03}.json.gz", chapter_num);
        write_json_gz(
          &chapters_dir.join(padded),
          &serde_json::Value::Object(chapter_data),
        )?;
      }

      chs.len()
    } else {
      0
    };

    let stats = compute_novel_stats(precomputed);
    Ok((total_chapters, stats))
  })();

  // On failure, clean up temp dir
  let (total_chapters, stats) = match import_result {
    Ok(v) => v,
    Err(e) => {
      let _ = std::fs::remove_dir_all(&tmp_dir);
      return Err(e);
    }
  };

  // Success: atomically move temp → final location
  if is_dup && overwrite {
    let _ = std::fs::remove_dir_all(&novel_dir);
  }
  std::fs::rename(&tmp_dir, &novel_dir)
    .map_err(|e| {
      let _ = std::fs::remove_dir_all(&tmp_dir);
      format!("移动数据目录失败: {e}")
    })?;

  // Update user manifest.json
  update_manifest(&novels_dir, &slug, &title, author.as_deref(), total_chapters, &stats)?;

  Ok(ImportResult {
    slug,
    title,
    total_chapters,
  })
}

// ── Delete imported novel ─────────────────────────

/// Delete a user-imported novel (refuses to delete preinstalled novels)
#[tauri::command]
fn delete_imported_novel(app: tauri::AppHandle, slug: String) -> Result<(), String> {
  let novels_dir = get_novels_dir(&app)?;
  let novel_dir = novels_dir.join(&slug);

  // H1 fix: Path traversal protection — ensure resolved path stays within novels_dir
  let canonical_dir = novel_dir.canonicalize()
    .map_err(|_| "该小说数据不存在".to_string())?;
  let canonical_base = novels_dir.canonicalize()
    .map_err(|e| format!("无法解析数据目录: {e}"))?;
  if !canonical_dir.starts_with(&canonical_base) {
    return Err("不能删除预装小说".to_string());
  }

  // Safety: ensure the slug only belongs to user-imported data
  if is_preinstalled(&app, &slug) {
    return Err("不能删除预装小说".to_string());
  }

  // M1 fix: Update manifest BEFORE deleting data — if manifest write fails,
  // data is still intact and user can retry
  remove_from_manifest(&novels_dir, &slug)?;

  // Remove data directory
  std::fs::remove_dir_all(&canonical_dir)
    .map_err(|e| format!("删除数据失败: {e}"))?;

  Ok(())
}

/// Check if a slug belongs to a preinstalled (bundled) novel
fn is_preinstalled(app: &tauri::AppHandle, slug: &str) -> bool {
  if let Ok(bundled_path) = app
    .path()
    .resolve("resources/novels/manifest.json", tauri::path::BaseDirectory::Resource)
  {
    if let Ok(bytes) = std::fs::read(&bundled_path) {
      if let Ok(manifest) = serde_json::from_slice::<serde_json::Value>(&bytes) {
        if let Some(novels) = manifest.get("novels").and_then(|v| v.as_array()) {
          return novels.iter().any(|n| {
            n.get("slug").and_then(|v| v.as_str()) == Some(slug)
          });
        }
      }
    }
  }
  false
}

/// Remove a novel entry from the user manifest by slug
fn remove_from_manifest(novels_dir: &PathBuf, slug: &str) -> Result<(), String> {
  let manifest_path = novels_dir.join("manifest.json");
  if !manifest_path.exists() {
    return Ok(());
  }

  let bytes = std::fs::read(&manifest_path)
    .map_err(|e| format!("读取 manifest 失败: {e}"))?;
  let mut manifest: serde_json::Value = serde_json::from_slice(&bytes)
    .map_err(|e| format!("解析 manifest 失败: {e}"))?;

  if let Some(novels) = manifest.get_mut("novels").and_then(|v| v.as_array_mut()) {
    novels.retain(|n| {
      n.get("slug")
        .and_then(|s| s.as_str())
        .map(|s| s != slug)
        .unwrap_or(true)
    });
  }

  let json = serde_json::to_string_pretty(&manifest)
    .map_err(|e| format!("序列化 manifest 失败: {e}"))?;
  std::fs::write(&manifest_path, json)
    .map_err(|e| format!("写入 manifest 失败: {e}"))?;

  Ok(())
}

// ── Helper functions ──────────────────────────────

fn decompress_if_gzip(bytes: Vec<u8>) -> Result<String, String> {
  if bytes.len() >= 2 && bytes[0] == 0x1f && bytes[1] == 0x8b {
    let mut decoder = GzDecoder::new(&bytes[..]);
    let mut s = String::new();
    decoder
      .read_to_string(&mut s)
      .map_err(|e| format!("解压失败: {e}"))?;
    Ok(s)
  } else {
    String::from_utf8(bytes).map_err(|e| format!("UTF-8 解码失败: {e}"))
  }
}

fn read_and_parse_air(path: &str) -> Result<serde_json::Value, String> {
  let bytes = std::fs::read(path).map_err(|e| {
    if e.kind() == std::io::ErrorKind::NotFound {
      "文件不存在或无法访问".to_string()
    } else {
      format!("读取文件失败: {e}")
    }
  })?;

  // Must be gzip
  if bytes.len() < 2 || bytes[0] != 0x1f || bytes[1] != 0x8b {
    return Err("文件格式无效，不是合法的 .air 分析数据".to_string());
  }

  let mut decoder = GzDecoder::new(&bytes[..]);
  let mut json_str = String::new();
  decoder
    .read_to_string(&mut json_str)
    .map_err(|_| "文件格式无效，不是合法的 .air 分析数据".to_string())?;

  serde_json::from_str(&json_str)
    .map_err(|_| "数据解析失败，文件可能已损坏".to_string())
}

fn write_json_gz(path: &PathBuf, data: &serde_json::Value) -> Result<(), String> {
  let json_bytes = serde_json::to_vec(data)
    .map_err(|e| format!("序列化失败: {e}"))?;

  let file = std::fs::File::create(path)
    .map_err(|e| format!("写入数据失败: {e}"))?;

  let mut encoder = GzEncoder::new(file, Compression::fast());
  encoder
    .write_all(&json_bytes)
    .map_err(|e| format!("写入数据失败: {e}"))?;
  encoder
    .finish()
    .map_err(|e| format!("写入数据失败: {e}"))?;

  Ok(())
}

fn sanitize_slug(title: &str) -> String {
  // Remove whitespace and common punctuation, keep CJK + alphanumeric
  let slug: String = title
    .chars()
    .filter(|c| c.is_alphanumeric() || *c > '\u{2E7F}') // Keep CJK characters
    .collect();

  if slug.is_empty() {
    "unknown".to_string()
  } else {
    slug
  }
}

fn get_novels_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
  let data_dir = app
    .path()
    .app_data_dir()
    .map_err(|e| format!("获取数据目录失败: {e}"))?;
  let novels_dir = data_dir.join("novels");
  std::fs::create_dir_all(&novels_dir)
    .map_err(|e| format!("创建数据目录失败: {e}"))?;
  Ok(novels_dir)
}

fn check_duplicate(
  app: &tauri::AppHandle,
  novels_dir: &PathBuf,
  slug: &str,
  title: &str,
) -> (bool, Option<String>) {
  // 1. Check user data directory for existing slug
  let novel_dir = novels_dir.join(slug);
  if novel_dir.exists() {
    return (true, Some(slug.to_string()));
  }

  // 2. Check user manifest for title match with different slug
  if let Ok(manifest) = read_manifest(novels_dir) {
    if let Some(novels) = manifest.get("novels").and_then(|v| v.as_array()) {
      for novel in novels {
        if let Some(t) = novel.get("title").and_then(|v| v.as_str()) {
          if t == title {
            let s = novel
              .get("slug")
              .and_then(|v| v.as_str())
              .unwrap_or(slug)
              .to_string();
            return (true, Some(s));
          }
        }
      }
    }
  }

  // 3. Check bundled (preinstalled) manifest for title/slug conflict
  if let Ok(bundled_path) = app
    .path()
    .resolve("resources/novels/manifest.json", tauri::path::BaseDirectory::Resource)
  {
    if let Ok(bytes) = std::fs::read(&bundled_path) {
      if let Ok(manifest) = serde_json::from_slice::<serde_json::Value>(&bytes) {
        if let Some(novels) = manifest.get("novels").and_then(|v| v.as_array()) {
          for novel in novels {
            let bundled_title = novel.get("title").and_then(|v| v.as_str()).unwrap_or("");
            let bundled_slug = novel.get("slug").and_then(|v| v.as_str()).unwrap_or("");
            if bundled_title == title || bundled_slug == slug {
              return (true, Some(bundled_slug.to_string()));
            }
          }
        }
      }
    }
  }

  (false, None)
}

fn read_manifest(novels_dir: &PathBuf) -> Result<serde_json::Value, String> {
  let manifest_path = novels_dir.join("manifest.json");
  if !manifest_path.exists() {
    return Ok(serde_json::json!({ "version": 1, "novels": [] }));
  }
  let bytes = std::fs::read(&manifest_path)
    .map_err(|e| format!("读取 manifest 失败: {e}"))?;
  serde_json::from_slice(&bytes)
    .map_err(|e| format!("解析 manifest 失败: {e}"))
}

#[derive(Serialize, Deserialize, Clone)]
struct ManifestNovel {
  slug: String,
  title: String,
  #[serde(skip_serializing_if = "Option::is_none")]
  author: Option<String>,
  #[serde(rename = "totalChapters")]
  total_chapters: usize,
  #[serde(skip_serializing_if = "Option::is_none")]
  stats: Option<NovelStats>,
}

#[derive(Serialize, Deserialize, Clone)]
struct NovelStats {
  characters: u64,
  relations: u64,
  locations: u64,
  events: u64,
}

fn compute_novel_stats(precomputed: &serde_json::Value) -> NovelStats {
  let characters = precomputed
    .get("graph")
    .and_then(|g| g.get("nodes"))
    .and_then(|n| n.as_array())
    .map(|a| a.len() as u64)
    .unwrap_or(0);

  let relations = precomputed
    .get("graph")
    .and_then(|g| g.get("edges"))
    .and_then(|e| e.as_array())
    .map(|a| a.len() as u64)
    .unwrap_or(0);

  let locations = precomputed
    .get("map")
    .and_then(|m| m.get("locations"))
    .and_then(|l| l.as_array())
    .map(|a| a.len() as u64)
    .unwrap_or(0);

  let events = precomputed
    .get("timeline")
    .and_then(|t| t.get("events"))
    .and_then(|e| e.as_array())
    .map(|a| a.len() as u64)
    .unwrap_or(0);

  NovelStats {
    characters,
    relations,
    locations,
    events,
  }
}

fn update_manifest(
  novels_dir: &PathBuf,
  slug: &str,
  title: &str,
  author: Option<&str>,
  total_chapters: usize,
  stats: &NovelStats,
) -> Result<(), String> {
  let manifest_path = novels_dir.join("manifest.json");

  let mut manifest: serde_json::Value = if manifest_path.exists() {
    let bytes = std::fs::read(&manifest_path)
      .map_err(|e| format!("读取 manifest 失败: {e}"))?;
    serde_json::from_slice(&bytes)
      .map_err(|e| format!("解析 manifest 失败: {e}"))?
  } else {
    serde_json::json!({ "version": 1, "novels": [] })
  };

  let novels = manifest
    .get_mut("novels")
    .and_then(|v| v.as_array_mut())
    .ok_or("manifest.json 格式错误")?;

  // Remove existing entry with same slug
  novels.retain(|n| {
    n.get("slug")
      .and_then(|s| s.as_str())
      .map(|s| s != slug)
      .unwrap_or(true)
  });

  // Add new entry
  let entry = ManifestNovel {
    slug: slug.to_string(),
    title: title.to_string(),
    author: author.map(|s| s.to_string()),
    total_chapters,
    stats: Some(stats.clone()),
  };

  novels.push(serde_json::to_value(entry).map_err(|e| format!("序列化失败: {e}"))?);

  let json = serde_json::to_string_pretty(&manifest)
    .map_err(|e| format!("序列化 manifest 失败: {e}"))?;
  std::fs::write(&manifest_path, json)
    .map_err(|e| format!("写入 manifest 失败: {e}"))?;

  Ok(())
}

fn build_chapter_facts_map(air_data: &serde_json::Value) -> std::collections::HashMap<u64, serde_json::Value> {
  let mut map = std::collections::HashMap::new();
  if let Some(facts) = air_data.get("chapter_facts").and_then(|v| v.as_array()) {
    for fact in facts {
      if let Some(ch_num) = fact.get("chapter_num").and_then(|v| v.as_u64()) {
        // Parse fact_json string into Value
        if let Some(fact_json_str) = fact.get("fact_json").and_then(|v| v.as_str()) {
          if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(fact_json_str) {
            map.insert(ch_num, parsed);
          }
        }
      }
    }
  }
  map
}

fn extract_chapter_entities(
  fact: &serde_json::Value,
) -> serde_json::Value {
  let mut entities = Vec::new();
  let mut seen = std::collections::HashSet::new();

  // Extract from characters
  if let Some(chars) = fact.get("characters").and_then(|v| v.as_array()) {
    for ch in chars {
      if let Some(name) = ch.get("name").and_then(|v| v.as_str()) {
        if seen.insert(name.to_string()) {
          entities.push(serde_json::json!({ "name": name, "type": "person" }));
        }
      }
    }
  }

  // Extract from locations
  if let Some(locs) = fact.get("locations").and_then(|v| v.as_array()) {
    for loc in locs {
      if let Some(name) = loc.get("name").and_then(|v| v.as_str()) {
        if seen.insert(name.to_string()) {
          entities.push(serde_json::json!({ "name": name, "type": "location" }));
        }
      }
    }
  }

  // Extract from item_events
  if let Some(items) = fact.get("item_events").and_then(|v| v.as_array()) {
    for item in items {
      if let Some(name) = item.get("item_name").and_then(|v| v.as_str()) {
        if seen.insert(name.to_string()) {
          entities.push(serde_json::json!({ "name": name, "type": "item" }));
        }
      }
    }
  }

  // Extract from org_events
  if let Some(orgs) = fact.get("org_events").and_then(|v| v.as_array()) {
    for org in orgs {
      if let Some(name) = org.get("org_name").and_then(|v| v.as_str()) {
        if seen.insert(name.to_string()) {
          entities.push(serde_json::json!({ "name": name, "type": "org" }));
        }
      }
    }
  }

  serde_json::Value::Array(entities)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_store::Builder::default().build())
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_notification::init())
    .plugin(tauri_plugin_shell::init())
    .manage(Mutex::new(SidecarState { port: None, child: None, starting: false }))
    .invoke_handler(tauri::generate_handler![
      load_resource,
      load_file_absolute,
      get_user_novels_dir,
      preview_air_file,
      import_air_file,
      delete_imported_novel,
      sidecar_start,
      sidecar_stop,
      sidecar_status,
    ])
    .setup(|app| {
      app.handle().plugin(
        tauri_plugin_log::Builder::default()
          .level(log::LevelFilter::Info)
          .build(),
      )?;

      // Handle file association: when app is launched via double-clicking a .air file,
      // the file path is passed as a CLI argument
      let args: Vec<String> = std::env::args().collect();
      if args.len() > 1 {
        let file_path = args[1].clone();
        if file_path.ends_with(".air") && std::path::Path::new(&file_path).exists() {
          let handle = app.handle().clone();
          // Delay emit so frontend has time to mount
          std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(800));
            let _ = handle.emit("novel:file-open", &file_path);
          });
        }
      }

      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|handle, event| {
      match event {
        // Handle file-open when app is already running (macOS "Open With")
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Opened { urls } => {
          for url in &urls {
            if let Ok(path) = url.to_file_path() {
              if let Some(path_str) = path.to_str() {
                if path_str.ends_with(".air") {
                  let _ = handle.emit("novel:file-open", path_str);
                }
              }
            }
          }
        }
        // Clean up sidecar on app exit
        tauri::RunEvent::ExitRequested { .. } => {
          let mutex: &Mutex<SidecarState> = handle.state::<Mutex<SidecarState>>().inner();
          if let Ok(mut s) = mutex.lock() {
            if let Some(child) = s.child.take() {
              let _ = child.kill();
              log::info!("Sidecar killed on app exit");
            }
            s.port = None;
          }
        }
        _ => {}
      }
    });
}
