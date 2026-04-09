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
    pub fn load(_path: &str, _save_folder: Option<&str>) -> Result<Self, String> {
        Err("config loading not yet implemented".to_string())
    }
}
