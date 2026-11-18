# Cooler Panel Auto-Tester v0.3.0

نرم‌افزار دسکتاپ برای تست رگرسیون Firmware پنل کولر مبتنی بر **STM8L052R8**. هدف نسخهٔ فعلی، فلش معتبر از طریق **ST-Link/SWIM** و شبیه‌سازی درایور موتور روی **USART1** است.

## وضعیت پیاده‌سازی در نسخهٔ 0.3.0

| قابلیت | وضعیت |
|---|---|
| پیدا کردن STVP_CmdLine و اجرای CLI | پیاده‌سازی شده؛ نیازمند تست روی ST-Link واقعی شما |
| اعتبارسنجی Intel HEX و Motorola S-record | پیاده‌سازی شده |
| رد کردن فایل IAR `.out` | پیاده‌سازی شده |
| Flash + Verify با STVP | پیاده‌سازی شده؛ اجرای واقعی روی سخت‌افزار در این محیط قابل تأیید نیست |
| Fake Driver با پروتکل 8 بایتی Firmware | جدول آدرس IMC نسخه 1 اصلاح و unit test دارد |
| رجیسترهای مشترک PUMP/VALVE و SPEED/TORQUE/POWER | مدل‌سازی به‌صورت رجیسترهای alias با وضعیت مشترک |
| تطبیق پاسخ با درخواست | CRC، کد پاسخ، آدرس، app_id و reg_index بررسی می‌شود |
| آدرس ناشناخته در READ/WRITE | پاسخ ساختگی ACK تولید نمی‌شود |
| هم‌ترازی مجدد فریم بعد از نویز/بایت گم‌شده | پیاده‌سازی شده |
| READ_STATUS و CLEAR_STATUS | مطابق تفسیر Firmware اصلاح شده |
| Fault injection و کنترل تبادل UART | سناریوهای اولیه دارد |
| قطع پاسخ درایور | سناریو باید ارسال مجدد همان درخواست را روی UART مشاهده کند؛ بیت داخلی Connection_Fault مستقیماً قابل خواندن نیست |
| Reset مستقل از طریق SWIM CLI | فعلاً Skip؛ نیازمند NRST کنترل‌شده یا API سطح پایین SWIM |
| RAM poke برای Power | تزریق دستی از Live Watch در IAR؛ تستر توالی واقعی فرمان‌های راه‌اندازی و پاسخ‌های Fake Driver را اعتبارسنجی می‌کند. تزریق خودکار هنوز پیاده‌سازی نشده است |
| گزارش JSON | ذخیرهٔ خودکار در پایان اجرا + امکان Save As |

این برنامه جایگزین تستر سخت‌افزاری تولید نیست. RF، بیزر، LCD، کلیدها و سنسورهای فیزیکی از محدودهٔ نسخهٔ فعلی خارج هستند.

## 1) آماده‌سازی خروجی IAR

در فایل پروژهٔ ارسال‌شده:

- کانفیگ **Debug** روی `STM8L052R8` است.
- خروجی HEX در Debug فعال است و نام فایل `FW_CoolerPanel_V4.x.hex` است.
- کانفیگ **Release** در حال حاضر روی `UnspecifiedSTM8` و linker عمومی تنظیم شده و ObjCopy/HEX آن غیرفعال است.
- خروجی `.out` فایل لینک‌شدهٔ IAR است و به‌جای HEX نباید به STVP داده شود.

برای اولین تست:

1. پروژه را در IAR باز کن.
2. از منوی Configuration، گزینهٔ **Debug** را انتخاب کن.
3. پروژه را Rebuild کن.
4. فایل `Debug\Exe\FW_CoolerPanel_V4.x.hex` را در نرم‌افزار انتخاب کن.
5. فایل MAP را نیز از خروجی Build نگه دار؛ در فایل RAR ارسالی MAP آماده وجود نداشت.

اگر قرار است از Release برای تست/تولید استفاده شود، قبل از Build باید Device آن نیز `STM8L052R8` و linker script آن `lnkstm8l052r8.icf` شود و تولید Intel HEX در ObjCopy فعال گردد. تنظیمات Release را بدون این اصلاح مبنا قرار نده.

## 2) ابزار فلش در ویندوز

روش پیشنهادی:

- **ST Visual Programmer STM8 (STVP)** را از ST نصب کن.
- فایل `STVP_CmdLine.exe` را انتخاب کن، یا مسیر آن را در PATH قرار بده.
- ابزار معمولاً در پوشه‌ای شبیه زیر قرار دارد:

```text
C:\Program Files (x86)\STMicroelectronics\st_toolset\stvp\STVP_CmdLine.exe
```

نرم‌افزار فرمانی با این ساختار اجرا می‌کند:

```powershell
STVP_CmdLine.exe -BoardName=ST-LINK -Port=USB -ProgMode=SWIM -Device=STM8L052R8 -verbose -no_log -no_progress -no_loop -verif -no_progData -no_progOption -FileProg=C:\ProgramData\CoolerPanelAutoTester\stage\firmware.hex
```

فایل Firmware پیش از اجرای STVP به مسیری کوتاه و بدون فاصله/حروف غیر ASCII کپی می‌شود تا مشکل parser بعضی نسخه‌های قدیمی STVP با `-FileProg` رخ ندهد. فقط Program Memory فلش می‌شود؛ Data EEPROM و Option Bytes عمداً در این مرحله نوشته نمی‌شوند.

STVP پس از اجرا باید هر دو پیام `Programming PROGRAM MEMORY succeeds` و `Verifying PROGRAM MEMORY succeeds` را چاپ کند. صرفاً exit code صفر برای Pass کردن تست فلش کافی نیست.

### چرا `STM8_Programmer_CLI` استفاده نمی‌شود؟

نام و آرگومان `STM8_Programmer_CLI -c port=SWIM -w ... -rst` در نسخهٔ اولیه، متعلق به رابط معتبر STVP برای STM8 نبود. مسیر ویندوز این پروژه با `STVP_CmdLine.exe` پیاده‌سازی شده است. گزینهٔ `-rst` نیز به STVP CLI افزوده نشده، چون دستور مستند مستقلی برای reset/run هدف در CLI آن پیدا نشد.

## 3) اتصال ارتباط سریال

از سورس `initialization.c`:

- درایور موتور روی **USART1** است.
- Baud: **9600**
- Format: **8 data bits, no parity, 1 stop bit (8N1)**
- USART1 RX = **PC2**
- USART1 TX = **PC3**
- جهت TX/RX با GPIO **PE6** از طریق `CMD_USART_DIR` تغییر می‌کند.
- USART2 روی 115200 برای ماژول ESP استفاده می‌شود؛ آن را به‌عنوان پورت Fake Driver انتخاب نکن.

اتصال منطقی سمت مبدل، در صورتی که PC2/PC3 روی برد به‌صورت TTL 3.3V در دسترس باشند:

```text
USB-Serial TX  -> panel USART1 RX (PC2)
USB-Serial RX  <- panel USART1 TX (PC3)
USB-Serial GND --- panel GND (فقط در صورت ایمن و ایزوله بودن سیستم)
```

**مهم:** در سورس، PE6 هنگام ارسال/دریافت تغییر وضعیت می‌دهد؛ بنابراین پیش از اتصال مستقیم مبدل TTL باید با شماتیک مشخص شود که USART1 به ترنسیور نیمه‌دوطرفه/RS-485 یا مدار واسط دیگری وصل است یا نه. اگر سمت سوکت برد A/B دیفرانسیلی باشد، مبدل TTL مستقیم مناسب نیست و باید رابط متناظر استفاده شود. درایور واقعی باید از مسیر باس جدا شود تا دو فرستنده هم‌زمان روی خط نباشند.

### ایمنی الکتریکی

پنل کولر ممکن است در مدار متصل به برق شهر باشد. اتصال زمین USB کامپیوتر یا ST-Link به بردِ غیرایزوله می‌تواند خطرناک باشد. تست را فقط با منبع و فیکسچر ایزوله و با رعایت طراحی برد انجام بده؛ به‌هیچ‌وجه GND کامپیوتر را به برد برق‌شهرِ غیرایزوله وصل نکن. سطح منطق UART باید با بررسی سخت‌افزار 3.3V تأیید شود.

## 4) نصب و اجرا در ویندوز

```powershell
cd cooler_panel_tester
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

در UI:

1. HEX معتبر IAR Debug را انتخاب کن.
2. مسیر `STVP_CmdLine.exe` را در صورت پیدا نشدن خودکار تعیین کن.
3. MCU را `STM8L052R8` نگه دار.
4. پورت COM مبدل متصل به USART1 و Baud=9600 را انتخاب کن.
5. Start را بزن.

## 5) سناریوهای نسخهٔ فعلی

| ID | سناریو | نوع ارزیابی |
|---|---|---|
| T01 | شناسایی ابزار پروگرام | وجود CLI |
| T02 | اتصال USB-Serial | باز شدن COM |
| T03 | فلش و Verify | Program + Verify از STVP |
| T04 | Reset سخت‌افزاری هدف | Skip تا افزودن NRST/API SWIM |
| T05 | توالی راه‌اندازی POWER | پس از تزریق دستی `keyHandle.key[3]` در IAR، توالی PUMP، CTRL_MODE_SELECT، SPEED و STOP_START=1 را با پاسخ‌های متناظر بررسی می‌کند |
| T06 | دریافت فریم از پنل | تطبیق CRC، command response و هویت درخواست/پاسخ |
| T07 | پاسخ READ_STATUS | تطبیق فرمان، آدرس و پاسخ متناظر |
| T08 | تزریق Over-Temp | برگرداندن بیت Fault؛ در صورت فعال بودن Run بررسی STOP |
| T09 | پاک‌سازی Fault | مشاهده CLEAR_STATUS واقعی صادرشده از Firmware |
| T10 | قطع پاسخ Fake Driver | مشاهدهٔ حداقل دو درخواست همسان بدون پاسخ برای تأیید Retry؛ نه اثبات بیت داخلی Connection_Fault |
| T11 | خواندن DC Bus | پاسخ به READ واقعی پنل، در صورت درخواست رجیستر |

تست T05 از اپراتور می‌خواهد هنگام انتظار ۳۰ ثانیه‌ای، تزریق دستی RAM را در IAR انجام دهد؛ آدرس و مقادیر دقیق ساختار `keyHandle` باید با MAP همان Build بررسی شوند. این تست تزریق خودکار انجام نمی‌دهد و روشن‌شدن فیزیکی موتور را نیز اثبات نمی‌کند. تست‌های T06 به بعد به فعالیت پنل روی USART1 وابسته‌اند. برای T11 ممکن است نیاز باشد صفحهٔ پارامترهای نمایشگر را باز کنی تا Firmware رجیستر DC Bus را بخواند.

## 6) گزارش و لاگ

- گزارش JSON پس از هر اجرا در پوشهٔ `reports` ساخته می‌شود.
- از دکمهٔ «ذخیره گزارش JSON» می‌توان نسخه‌ای در مسیر دلخواه ذخیره کرد.
- لاگ شامل فرمان‌های RX/TX، فریم خام، نتیجهٔ پروگرام و خطاهای COM است.

## 7) تست واحد بدون سخت‌افزار

```powershell
python -m unittest discover -s tests -v
```

این تست‌ها فقط اعتبارسنجی HEX، ساخت فرمان STVP و منطق Fake Driver را روی میزبان بررسی می‌کنند؛ جای تست واقعی ST-Link/برد را نمی‌گیرند.

## 8) تغییرات نسخهٔ 0.3.0

- جدول `ADDRESS_TABLE_V1` با جدول IMC_CODE_VERSION=1 در Firmware همسان شد.
- آدرس مشترک PUMP/VALVE با بیت‌های `0x10` (پمپ) و `0x20` (شیر) مدل می‌شود.
- آدرس مشترک SPEED/TORQUE/POWER یک مقدار مشترک دارد.
- پاسخ‌ها با درخواست اصلی از نظر CRC، command response، address، app_id و reg_index تطبیق داده می‌شوند.
- برای READ/WRITE روی آدرس ناشناخته، Fake Driver دیگر ACK صوری تولید نمی‌کند.
- T10 اکنون تکرار همان درخواست را در زمان قطع پاسخ بررسی می‌کند.
- T05 از حالت Skip خارج شده است: تستر پس از تزریق دستی RAM در IAR، توالی فرمان‌های واقعی POWER را بررسی می‌کند. خود تزریق هنوز خودکار نیست.

## 9) موارد باز برای فاز بعد

1. اتصال NRST به یک خروجی کنترل‌شده یا پیاده‌سازی reset با SWIM سطح پایین.
2. پیاده‌سازی halt/read/write/run RAM برای Power poke بر اساس فایل MAP بیلد IAR؛ فایل MAP در آرشیو ورودی وجود نداشت.
3. اضافه کردن قابلیت انتخاب رجیستر/مقادیر Fault و Warning از UI.
4. افزودن سناریوهای درصد سرعت، Pump/Valve، LIMIT_FLAG و Driver temperature براساس درخواست‌های واقعی USART.
5. بررسی شماتیک برای تعیین اینکه اتصال مبدل به USART1 باید TTL یا رابط دیفرانسیلی باشد.

## ساختار پروژه

```text
cooler_panel_tester/
  main.py
  requirements.txt
  README.md
  app/
    config.py
    driver/       # پروتکل و Fake Driver
    stlink/       # اعتبارسنجی و فلش/Verify
    engine/       # سناریوها و Test Engine
    ui/           # PySide6 UI
  tests/          # unit tests بدون سخت‌افزار
  reports/        # گزارش‌های JSON اجرای تست
```
