# Module Hub

GUI local để kết nối các module đã PASS thành pipeline mà không sửa lõi của từng repo.

## MVP v0.1.0

Module đã nối:

- TikTok LIVE middleware (`banupham/tiktok_live_cmd_active_viewers_v9`)
- Google STT Bridge v2.6 (`banupham/google-stt-bridge`)
- Google Translate RPC V2 Final (`banupham/google-translate-rpc`)
- Google TTS API + Speaker (`banupham/google_loa_tts`)

Pipeline có sẵn:

- TikTok → TTS
- TikTok → Translate → TTS
- STT → Translate → TTS

## Cách chạy nhanh trên Windows

Đặt các repo cạnh nhau là dễ nhất:

```text
work/
├── module-hub/
├── tiktok_live_cmd_active_viewers_v9/
├── google-stt-bridge/
├── google-translate-rpc/
└── google_loa_tts/
```

Sau đó chạy:

```cmd
RUN.cmd
```

Mở:

```text
http://127.0.0.1:8899
```

Nếu repo nằm chỗ khác, nhập đường dẫn trong giao diện rồi bấm **Lưu đường dẫn**.

## Port mặc định

| Module | Port |
|---|---:|
| Module Hub | 8899 |
| TikTok middleware API | 8787 |
| Google STT | 8091 |
| Google Translate | 8080 |
| Google TTS API | 8090 |
| Google TTS Speaker | 9000 |

## Cách Hub nối TikTok

Hub khởi động `start_live.bat` với biến môi trường:

```text
WEBHOOK_URLS=http://127.0.0.1:8899/tiktok-event
COLLECTOR_MODE=direct
```

Vì vậy middleware vẫn dùng normalizer và collector đã PASS; Hub chỉ là webhook đích.

## Cách Hub nối STT

Hub long-poll:

```text
GET http://127.0.0.1:8091/api/result?after=N&timeout=20
```

Mỗi sentence được chuẩn hóa thành một text event nội bộ.

## Cách Hub nối Translate

```text
POST http://127.0.0.1:8080/translate
```

Mặc định `from=auto`, `mode=advanced`.

## Cách Hub nối TTS

Hub gửi comment đã xử lý tới Speaker:

```text
POST http://127.0.0.1:9000/tiktok-event
```

Speaker giữ queue FIFO và gọi TTS API `:8090/tts` như thiết kế đã PASS.

## Nguyên tắc

- Module đã PASS không bị copy/chỉnh lõi vào Hub.
- Hub chỉ quản lý process, health check, adapter và pipeline.
- `config.local.json` chứa đường dẫn máy local và không commit.
- Log process nằm trong `logs/` và không commit.
