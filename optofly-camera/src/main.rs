mod config;
mod buffer;
mod capture;
mod encoder;
mod metadata;

use clap::Parser;
use std::sync::atomic::{AtomicBool, Ordering};

pub static SHUTDOWN: AtomicBool = AtomicBool::new(false);

#[derive(Parser, Debug)]
#[command(name = "optofly-camera", about = "High-speed XIMEA camera capture")]
struct Args {
    /// Path to config TOML file
    #[arg(short, long, default_value = "configs/config.toml")]
    config: String,

    /// Save folder for video output (overrides config)
    #[arg(short, long)]
    save_folder: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    log_level: String,
}

fn main() {
    let args = Args::parse();
    env_logger::Builder::new()
        .filter_level(args.log_level.parse().unwrap_or(log::LevelFilter::Info))
        .init();

    ctrlc::set_handler(|| {
        log::info!("Shutdown signal received");
        SHUTDOWN.store(true, Ordering::SeqCst);
    })
    .expect("Error setting signal handler");

    log::info!("optofly-camera starting with config: {}", args.config);

    let cfg = config::AppConfig::load(&args.config, args.save_folder.as_deref())
        .expect("Failed to load config");

    if let Err(e) = capture::run(cfg) {
        log::error!("Capture failed: {}", e);
        std::process::exit(1);
    }
}
