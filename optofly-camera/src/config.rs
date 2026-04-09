use serde::Deserialize;
use std::fs;
use std::path::Path;

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
    save_folder: Option<String>,
}

fn default_exposure() -> f32 { 2000.0 }
fn default_max_recording_time() -> f32 { 3.0 }

#[derive(Debug, Deserialize)]
struct ZmqToml {
    #[serde(default = "default_trigger_port")]
    trigger_port: u16,
    #[serde(default = "default_zone_enter_topic")]
    zone_enter_topic: String,
    #[serde(default = "default_zone_exit_topic")]
    zone_exit_topic: String,
}

fn default_trigger_port() -> u16 { 5556 }
fn default_zone_enter_topic() -> String { "ZONE_ENTER".to_string() }
fn default_zone_exit_topic() -> String { "ZONE_EXIT".to_string() }

pub struct AppConfig {
    pub width: u32,
    pub height: u32,
    pub fps: f32,
    pub exposure_us: f32,
    pub max_recording_time: f32,
    pub save_folder: String,
    pub zmq_trigger_address: String,
    pub zmq_zone_enter_topic: String,
    pub zmq_zone_exit_topic: String,
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

        // Ensure save folder exists
        fs::create_dir_all(Path::new(&save_folder))
            .map_err(|e| format!("Cannot create save folder {}: {}", save_folder, e))?;

        Ok(AppConfig {
            width: cam.resolution[0],
            height: cam.resolution[1],
            fps: cam.fps,
            exposure_us: cam.exposure_time,
            max_recording_time: cam.max_recording_time,
            save_folder,
            zmq_trigger_address: format!("tcp://localhost:{}", zmq.trigger_port),
            zmq_zone_enter_topic: zmq.zone_enter_topic,
            zmq_zone_exit_topic: zmq.zone_exit_topic,
        })
    }

    /// Total buffer capacity in frames (max_recording_time + 1s margin).
    pub fn buffer_capacity(&self) -> usize {
        ((self.max_recording_time + 1.0) * self.fps) as usize
    }

    /// Bytes per frame (grayscale).
    pub fn frame_bytes(&self) -> usize {
        self.width as usize * self.height as usize
    }
}
