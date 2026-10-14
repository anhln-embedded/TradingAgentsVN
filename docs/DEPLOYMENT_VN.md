# TradingAgentsVN: cập nhật core và vận hành Linux 24/7

## Cấu trúc và quyết định thiết kế

```text
tradingagents/       Core của fork Việt Nam; tiếp tục nhận thay đổi upstream
cli/                CLI upstream
vnapp/              Adapter core, cấu hình đường dẫn, storage và giới hạn chạy
web/api/            HTTP API + chuyển kết quả Agent thành sự kiện SSE
web/static/         Giao diện riêng của TradingAgentsVN
deploy/             Dockerfile, Compose, mẫu biến môi trường cho Linux
tools/              Chuẩn bị cập nhật trong checkout riêng
tests_vn/           Kiểm thử hợp đồng web/core và runtime của bản Việt Nam
docs/               Hướng dẫn triển khai/vận hành
```

Không đổi tên hoặc di chuyển toàn bộ `tradingagents/`: fork hiện có 8 commit tích hợp
thị trường Việt Nam và phụ thuộc các tool/prompt/vendor trong core. Thay fork bằng
package upstream nguyên bản ngay lúc này sẽ mất các chức năng đó. Đây là cấu trúc
fork có lớp tích hợp riêng, chưa phải hệ plugin độc lập hoàn toàn.

`vnapp/core_adapter.py` sở hữu timeout, chuẩn hóa streaming và vị trí decision.
Web gọi adapter thay vì phụ thuộc trực tiếp `process_signal()` của core. Hai thay
đổi timeout trước đây trong file core đã được chuyển sang adapter. Khi upstream
thay đổi API, kiểm thử hợp đồng và kiểm thử Việt Nam phải chạy trước khi phát hành.
`vnapp/core-version.json` ghi SHA upstream đã tích hợp; không tự nhận SHA mới chỉ
vì đã fetch. File này không thay thế kiểm thử hồi quy.

Không chạy `git pull` trên container production. Image chứa source cố định tại
thời điểm build. Compose nằm riêng trong `deploy/` để upstream thay Dockerfile CLI
ở root không ghi đè cách chạy web. Git trên giao diện bị tắt trong production.

## Triển khai lần đầu

Điều kiện: Linux có Docker Engine và Docker Compose plugin; mã project đã được
commit/push vào repository riêng. Trên server, clone repository riêng của bạn,
không clone upstream thuần vì upstream không có web và phần Việt Nam.

Từ thư mục gốc project:

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Sửa `deploy/.env`: đặt `TA_WEB_PASSWORD` mạnh, username, provider, model, endpoint
và API key thật. Không commit file này. Nếu proxy AI đang dùng địa chỉ Tailscale,
server Linux phải truy cập được địa chỉ đó; `localhost` trong container là chính
container, không phải máy đang chạy proxy.

Ví dụ tên image của bản phát hành dùng commit project:

```bash
export APP_VERSION=$(git rev-parse --short HEAD)
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build --wait
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 -f dashboard
```

Không dùng `run_web.py` trên server: launcher đó dành cho local, mở browser và bật
reload. Container chạy Uvicorn không reload, một worker. Cần giữ một replica vì
core hiện dùng cấu hình process và khóa phân tích nằm trong bộ nhớ process.

Port chỉ bind `127.0.0.1:8000` trên server. Để truy cập riêng qua SSH:

```bash
ssh -L 8000:127.0.0.1:8000 user@your-server
```

Mở `http://127.0.0.1:8000` trên máy của bạn và đăng nhập bằng username/password đã
đặt. Nếu dùng domain công khai, đặt reverse proxy HTTPS phía trước port local.
Đặt `TA_PUBLIC_ORIGIN=https://your-domain` trong deploy/.env để kiểm tra Origin
đúng sau khi TLS kết thúc ở proxy. Proxy phải giữ header Authorization, không buffer SSE, và cho phép kết nối SSE
dài; không mở Basic Auth trực tiếp qua HTTP công khai. Ví dụ Nginx trong `location /`:

```nginx
proxy_pass http://127.0.0.1:8000;
proxy_http_version 1.1;
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_buffering off;
proxy_read_timeout 3600s;
```

Chỉ tin header proxy từ reverse proxy của bạn; Uvicorn mặc định tin địa chỉ loopback,
nhưng đường đi qua Docker bridge có thể cần cấu hình `--forwarded-allow-ips` đúng
địa chỉ proxy. Nếu chưa thiết lập, dùng SSH tunnel trước. Phần HTTPS/domain phụ
thuộc server của bạn và chưa được triển khai trong repository này.

## Dữ liệu và trạng thái

Named volume `app_data` chứa:

```text
/data/results/              Báo cáo, snapshot mới nhất từng ticker, lịch sử run
/data/cache/                Cache core, checkpoint nếu bật
/data/memory/               Bộ nhớ quyết định Agent
/data/config/runtime.env    Thiết lập AI thay đổi từ giao diện
```

Đường dẫn không phụ thuộc thư mục shell. Thiết lập trong `runtime.env` được ưu
tiên hơn biến AI ban đầu từ `deploy/.env` sau khi container khởi động lại. Nếu muốn
quay về cấu hình AI trong deploy/.env, dừng app và sao lưu/xóa runtime.env trong
volume. Không xóa volume chỉ để đổi cấu hình.

Snapshots được ghi atomic bằng rename, giữ object JSON thay vì stringify mọi
trường. Lỗi ghi dữ liệu được trả về `run_error`, không báo hoàn tất giả.
Mỗi process chỉ nhận một phiên phân tích cùng lúc; phiên thứ hai nhận HTTP 409.
Đổi cấu hình AI bị chặn khi đang phân tích. Đóng browser không hủy worker; worker
tiếp tục lưu báo cáo. Bộ đệm SSE có giới hạn 256 sự kiện, sự kiện cũ có thể bị bỏ
khi browser đọc chậm; kết quả cuối vẫn được lưu để chọn lại ticker xem.

Docker tự khởi động lại khi process thoát, xoay log và kiểm tra `/api/health`.
Healthcheck báo `unhealthy` **không tự restart** process bị treo; restart policy
xử lý process thoát. Cần giám sát health ở server nếu muốn cảnh báo/tự khôi phục
khi treo. `/api/health` chỉ kiểm tra API process, không kiểm tra provider AI.

Shutdown thông thường chờ worker tối đa 300 giây để lưu. Mất điện, kill cưỡng bức
hoặc run dài hơn thời gian đó vẫn có thể mất run đang thực hiện. Dữ liệu đã lưu
trong volume được giữ. Chưa có hàng đợi durable/resume tự động sau restart và chưa
có lịch phân tích định kỳ; “24/7” ở đây là dịch vụ web luôn sẵn sàng nhận yêu cầu.

## Cập nhật upstream

Trước lần đầu, lưu các file mới của bản Việt Nam vào Git bằng danh sách rõ ràng:

```bash
git add .gitignore .dockerignore vnapp web deploy tools docs tests_vn tests/test_dashboard_summary.cjs .github/workflows/vn-dashboard.yml run_web.py
git diff --cached --stat
git commit -m "Separate VN web integration and add Linux deployment"
```

Nếu muốn giữ `run_bsr.py`/`run_plx.py`, kiểm tra nội dung không có secret rồi thêm
chúng vào commit riêng. Script cập nhật yêu cầu working tree sạch, gồm cả file
chưa theo dõi, để tránh bỏ sót code riêng.

Chuẩn bị cập nhật trên máy phát triển hoặc CI, không phải container đang phục vụ:

```bash
python tools/prepare_upstream.py --ref main --name upgrade-20261014
```

Script clone repo hiện tại vào `.updates/upgrade-20261014`, tạo nhánh
`codex/update-upstream`, fetch upstream và merge SHA đã fetch trong bản sao.
Checkout gốc không đổi. Nếu xung đột, script dừng và giữ candidate để sửa thủ công.
Không bỏ qua xung đột bằng `-X theirs` hoặc ghi đè toàn bộ core.

Trong candidate, tạo môi trường kiểm thử riêng và cài lại dependencies để tránh
test vô tình dùng package editable của checkout cũ:

```bash
cd .updates/upgrade-20261014
python3 -m venv .venv
. .venv/bin/activate
python -m pip install . -r web/requirements.txt pytest httpx
python -m unittest discover -s tests_vn -v
node tests/test_dashboard_summary.cjs
python -m pytest tests
```

Nếu merge xung đột cần giải quyết và commit merge trước; sau đó cập nhật
`vnapp/core-version.json` bằng SHA `FETCH_HEAD` đã tích hợp. Sau merge tự động,
script đã sửa manifest và cần commit file đó. Kiểm tra thêm thật ít nhất một mã
Việt Nam (ví dụ MBB), việc lưu/xem lại báo cáo và SSE với provider đang sử dụng.
Test mock xác nhận hợp đồng, không chứng minh mọi provider AI hoạt động.

Đưa candidate thành nhánh review ở repository riêng, chỉ tích hợp vào nhánh phát
hành sau khi CI/test qua. Workflow `VN dashboard contract and container` chạy
kiểm thử adapter, dashboard, hồi quy Việt Nam và build/start container trên Linux.
CI chưa tự deploy lên server.

## Phát hành và rollback

Giữ tên image cũ trước khi cập nhật, chờ phiên phân tích hiện tại hoàn tất rồi
backup volume. Checkout bản phát hành đã được kiểm thử trên server; build image
với `APP_VERSION` mới và chạy Compose như lệnh triển khai lần đầu.

Rollback dùng image cũ còn giữ trên server, không rebuild từ source mới:

```bash
export APP_VERSION=<old-version>
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait
```

Không chạy `docker image prune -a` nếu cần giữ image rollback. Bản dependency
được resolve lúc build; hiện chưa có lockfile đa nền tảng. Giữ image đã kiểm thử
hoặc push vào registry riêng để rollback đúng artifact; rebuild cùng SHA có thể
lấy phiên bản dependency mới do constraints upstream dùng `>=`.

## Backup dữ liệu

Đợi hết phân tích, dừng app để tránh snapshot/cache/memory thay đổi lúc backup:

```bash
mkdir -p backups
docker compose --env-file deploy/.env -f deploy/compose.yaml stop dashboard
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  --user 0 --entrypoint tar -v "$PWD/backups:/backup" dashboard \
  -czf /backup/tradingagents-data.tgz -C /data .
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
```

Archive chứa runtime.env có thể chứa API key: giữ riêng và bảo vệ như file secret.
Sao lưu cả `deploy/.env` ở nơi bảo mật. Restore sau khi dừng app bằng container
one-off cùng volume, giải nén archive vào `/data`, giữ quyền UID 10001, rồi khởi
động lại. Không dùng `docker compose down -v` nếu muốn giữ dữ liệu.

## Trạng thái xác minh bản refactor

Kiểm thử Python/API và JavaScript có thể chạy local không gọi AI thật. Máy làm
việc hiện chưa có Docker nên build/start container, quyền volume Linux và reverse
proxy phải được xác minh qua workflow CI hoặc server trước khi đưa vào sử dụng.
Chưa merge 135 commit upstream trong lần refactor này; tránh gộp đổi kiến trúc với
một đợt nâng core lớn mà chưa kiểm thử.

Tham khảo chính thức: [Docker Compose services](https://docs.docker.com/reference/compose-file/services/),
[Docker restart policy](https://docs.docker.com/engine/containers/start-containers-automatically/),
[FastAPI containers](https://fastapi.tiangolo.com/deployment/docker/).
