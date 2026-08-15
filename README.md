# Module Hub

GUI local để kết nối các module đã PASS thành pipeline mà không trộn lõi của từng repo.

## MVP v0.2.0 — Dynamic Port

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

Sau đó:

```cmd
RUN.cmd
```

Hub mặc định mở ở:

```text
http://127.0.0.1:8899
```

Muốn đổi cổng của chính Hub:

```cmd
RUN.cmd 18899
```

Nếu repo nằm chỗ khác, nhập đường dẫn trong giao diện rồi bấm **Lưu đường dẫn**.

## Cổng module không còn cố định

Hub không giả định STT phải là `8091`, Translate phải là `8080`, TTS phải là `8090/9000`.

Khi START pipeline:

```text
Hub
 ↓ hỏi hệ điều hành port đang trống
PortAllocator
 ↓
STT       : random-free-port
Translate : random-free-port
TTS API   : random-free-port
Speaker   : random-free-port
TikTok API: random-free-port
```

Sau đó Hub lưu runtime endpoint của từng module và mọi adapter đều tra endpoint này trước khi gọi.

Ví dụ một phiên có thể thành:

```text
Hub        :8899
TikTok     :52143
Translate  :52191
TTS API    :52207
Speaker    :52231
```

Lần chạy sau các port có thể khác.

## Module vẫn chạy độc lập được

Dynamic port không phá cách chạy cũ. Nếu chạy tay mà không truyền port, module vẫn giữ port mặc định cũ.

Các launcher đã hỗ trợ truyền port:

```cmd
REM STT
START_MIC.cmd 18091
START_PC_AUDIO.cmd 18091
START_SERVER_ONLY.cmd 18091

REM Translate
START_API.cmd 18080

REM Google TTS API
START_TTS_API.cmd 18090

REM Speaker: tham số 1 = speaker port, tham số 2 = TTS API port
START_SPEAKER.cmd 19000 18090
```

TikTok middleware vốn đã hỗ trợ `API_PORT` và `WEBHOOK_URLS` qua environment nên Hub cấp trực tiếp hai giá trị này khi start.

## Kết nối module đã chạy ngoài Hub

Nếu một module đã được chạy thủ công, không cần tắt để Hub tự start lại.

Trong card module:

1. nhập port đang chạy;
2. bấm **Kết nối port**;
3. Hub health-check endpoint đó;
4. pipeline dùng endpoint đã attach.

Hub không kill process bên ngoài khi disconnect.

## Cách Hub nối TikTok

Hub cấp hai giá trị runtime:

```text
API_PORT=<dynamic-port>
WEBHOOK_URLS=http://127.0.0.1:<hub-port>/tiktok-event
```

Middleware vẫn dùng collector + normalizer đã PASS; Hub chỉ là webhook destination.

## Cách Hub nối STT

Hub lấy runtime port rồi long-poll:

```text
GET http://127.0.0.1:<stt-port>/api/result?after=N&timeout=20
```

## Cách Hub nối Translate

```text
POST http://127.0.0.1:<translate-port>/translate
```

## Cách Hub nối TTS

Hub khởi động TTS API trước, sau đó truyền endpoint thực tế sang Speaker:

```text
LOA_TTS_API_URL=http://127.0.0.1:<tts-api-port>/tts
LOA_TTS_HEALTH_URL=http://127.0.0.1:<tts-api-port>/health
LOA_API_PORT=<speaker-port>
```

Hub gửi event cuối cùng tới:

```text
POST http://127.0.0.1:<speaker-port>/tiktok-event
```

## Nguyên tắc

- Bản module đã PASS vẫn giữ logic lõi.
- Mỗi module phải cho phép cấu hình port bằng CLI hoặc environment.
- Hub sở hữu việc cấp port khi Hub quản lý process.
- Adapter không chứa port cố định.
- Module chạy ngoài Hub có thể attach bằng port thủ công.
- `config.local.json` chứa đường dẫn máy local và không commit.
- Log process nằm trong `logs/` và không commit.
