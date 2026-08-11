use serde::Deserialize;
use std::fs;

#[derive(Debug, Deserialize)]
struct TomlRoot {
    camera: CameraToml,
    zmq: ZmqToml,
}

#[derive(Debug, Deserialize)]
struct CameraToml {
    resolution: [u32; 2],
    fps: f32,
    #[serde(default = "default_exposure")]
    exposure_time: f32,
    #[serde(default = "default_max_recording_time")]
    max_recording_time: f32,
    #[serde(default = "default_buffers_queue_size")]
    buffers_queue_size: i32,
    save_folder: Option<String>,
    #[serde(default)]
    aeag: bool,
    #[serde(default = "default_aeag_level")]
    aeag_level: i32,
    /// Max exposure limit for AE in µs. Defaults to 95% of the frame period.
    ae_max_limit: Option<f32>,
}

fn default_exposure() -> f32 { 2000.0 }
fn default_max_recording_time() -> f32 { 3.0 }
fn default_buffers_queue_size() -> i32 { 32 }
fn default_aeag_level() -> i32 { 50 }

#[derive(Debug, Deserialize)]
struct ZmqToml {
    #[serde(default = "default_trigger_port")]
    trigger_port: u16,
    #[serde(default = "default_zone_enter_topic")]
    zone_enter_topic: String,
    #[serde(default = "default_zone_exit_topic")]
    zone_exit_topic: String,
    #[serde(default = "default_opto_enter_topic")]
    opto_enter_topic: String,
    #[serde(default = "default_visual_enter_topic")]
    visual_enter_topic: String,
}

fn default_trigger_port() -> u16 { 5556 }
fn default_zone_enter_topic() -> String { "ZONE_ENTER".to_string() }
fn default_zone_exit_topic() -> String { "ZONE_EXIT".to_string() }
fn default_opto_enter_topic() -> String { "OPTO_ZONE_ENTER".to_string() }
fn default_visual_enter_topic() -> String { "VISUAL_ZONE_ENTER".to_string() }

pub struct AppConfig {
    pub width: u32,
    pub height: u32,
    pub fps: f32,
    pub exposure_us: f32,
    pub max_recording_time: f32,
    pub buffers_queue_size: i32,
    pub save_folder: String,
    pub zmq_trigger_address: String,
    pub zmq_zone_enter_topic: String,
    pub zmq_zone_exit_topic: String,
    pub zmq_opto_enter_topic: String,
    pub zmq_visual_enter_topic: String,
    pub aeag: bool,
    pub aeag_level: i32,
    /// Max AE exposure limit in µs (95% of frame period if not set in config).
    pub ae_max_limit: f32,
}

impl AppConfig {
    pub fn load(path: &str, save_folder_override: Option<&str>) -> Result<Self, String> {
        let contents = fs::read_to_string(path)
            .map_err(|e| format!("Cannot read config {}: {}", path, e))?;
        let root: TomlRoot = toml::from_str(&contents)
            .map_err(|e| format!("Cannot parse config: {}", e))?;

        let cam = root.camera;
        let zmq = root.zmq;

        let save_folder = save_folder_override
            .map(String::from)
            .or(cam.save_folder)
            .unwrap_or_else(|| "camera_videos".to_string());

        let ae_max_limit = cam.ae_max_limit
            .unwrap_or_else(|| 1_000_000.0 / cam.fps * 0.95);

        Ok(AppConfig {
            width: cam.resolution[0],
            height: cam.resolution[1],
            fps: cam.fps,
            exposure_us: cam.exposure_time,
            max_recording_time: cam.max_recording_time,
            buffers_queue_size: cam.buffers_queue_size,
            save_folder,
            zmq_trigger_address: format!("tcp://localhost:{}", zmq.trigger_port),
            zmq_zone_enter_topic: zmq.zone_enter_topic,
            zmq_zone_exit_topic: zmq.zone_exit_topic,
            zmq_opto_enter_topic: zmq.opto_enter_topic,
            zmq_visual_enter_topic: zmq.visual_enter_topic,
            aeag: cam.aeag,
            aeag_level: cam.aeag_level,
            ae_max_limit,
        })
    }

    /// Total buffer capacity in frames (max_recording_time + 1s margin).
    pub fn buffer_capacity(&self) -> usize {
        ((self.max_recording_time + 1.0) * self.fps) as usize
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn write_temp_toml(contents: &str) -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!(
            "optofly_camera_config_test_{}_{}.toml",
            std::process::id(),
            n
        ));
        std::fs::write(&path, contents).expect("write temp config");
        path.to_string_lossy().to_string()
    }

    #[test]
    fn opto_and_visual_enter_topics_default_when_absent() {
        let path = write_temp_toml(
            r#"
            [camera]
            resolution = [640, 480]
            fps = 100.0

            [zmq]
            "#,
        );
        let cfg = AppConfig::load(&path, None).expect("config should load");
        assert_eq!(cfg.zmq_opto_enter_topic, "OPTO_ZONE_ENTER");
        assert_eq!(cfg.zmq_visual_enter_topic, "VISUAL_ZONE_ENTER");
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn opto_and_visual_enter_topics_use_configured_value() {
        let path = write_temp_toml(
            r#"
            [camera]
            resolution = [640, 480]
            fps = 100.0

            [zmq]
            opto_enter_topic = "CUSTOM_OPTO"
            visual_enter_topic = "CUSTOM_VISUAL"
            "#,
        );
        let cfg = AppConfig::load(&path, None).expect("config should load");
        assert_eq!(cfg.zmq_opto_enter_topic, "CUSTOM_OPTO");
        assert_eq!(cfg.zmq_visual_enter_topic, "CUSTOM_VISUAL");
        std::fs::remove_file(&path).ok();
    }
}
