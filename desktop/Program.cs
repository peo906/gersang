using System;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [STAThread] private static void Main(string[] args)
    {
        bool created;
        using (var mutex = new Mutex(true, "Local\\GersangWorkshopDesktop", out created))
        {
            if (!created) { MessageBox.Show("제작으로부자되자이 이미 실행 중입니다."); return; }
            SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new WorkshopForm(Array.IndexOf(args, "--smoke-test") >= 0));
        }
    }
}

internal sealed class WorkshopForm : Form
{
    private const string LocalUrl = "http://127.0.0.1:8765";
    private readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    private readonly WebView2 calculator = new WebView2();
    private readonly HttpClient http = new HttpClient();
    private readonly bool smoke;
    private System.Diagnostics.Process backend;
    private bool closing;
    private bool ready;
    private readonly string token;

    internal WorkshopForm(bool smokeTest)
    {
        smoke = smokeTest;
        byte[] bytes = new byte[32];
        using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
        token = Convert.ToBase64String(bytes);
        http.Timeout = TimeSpan.FromSeconds(6);
        http.DefaultRequestHeaders.Add("X-Desktop-Token", token);
        Text = "제작으로부자되자";
        string iconPath = Path.Combine(root, "app.ico");
        if (File.Exists(iconPath)) Icon = new Icon(iconPath);
        Size = new Size(1500, 920);
        MinimumSize = new Size(1100, 720);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(14, 23, 27);
        calculator.Dock = DockStyle.Fill;
        var tools = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 40, Padding = new Padding(8, 4, 0, 0), BackColor = Color.FromArgb(21, 33, 39) };
        var refresh = new Button { Text = "계산 화면 새로고침", AutoSize = true, FlatStyle = FlatStyle.Flat, ForeColor = Color.White };
        refresh.Click += delegate { if (ready) calculator.Reload(); };
        var caption = new Label { Text = "외부 Edge에서 조회  /  조회 완료 후 1.5초 대기", AutoSize = true, ForeColor = Color.FromArgb(119, 220, 195), Padding = new Padding(12, 5, 0, 0) };
        tools.Controls.Add(refresh); tools.Controls.Add(caption);
        Controls.Add(calculator); Controls.Add(tools);
        Shown += async delegate { await Initialize(); };
        FormClosing += CloseApplication;
    }

    private async Task Initialize()
    {
        try
        {
            string runtimeFolder = null;
            string bundledRuntime = Path.Combine(root, "WebView2Runtime");
            bool installedRuntime = false;
            try { installedRuntime = !String.IsNullOrEmpty(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
            catch (WebView2RuntimeNotFoundException) { }
            if (Directory.Exists(bundledRuntime) && (smoke || !installedRuntime))
            {
                runtimeFolder = bundledRuntime;
                // Fixed Runtime on Windows 10 requires read access for AppContainer renderers.
                if (Environment.OSVersion.Version.Build < 22000)
                {
                    var directory = new DirectoryInfo(runtimeFolder);
                    var access = directory.GetAccessControl();
                    foreach (string sid in new[] { "S-1-15-2-1", "S-1-15-2-2" })
                        access.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid), FileSystemRights.ReadAndExecute,
                            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                    directory.SetAccessControl(access);
                }
            }
            if (runtimeFolder == null && !installedRuntime)
                throw new Exception("Microsoft Edge WebView2 Runtime이 필요합니다.");
            try
            {
                using (var check = new HttpClient { Timeout = TimeSpan.FromSeconds(1) })
                {
                    await check.GetAsync(LocalUrl + "/api/state");
                    throw new InvalidOperationException("기존 웹 프로그램이 실행 중입니다. 기존 실행 창에서 Ctrl+C로 종료한 뒤 앱을 다시 실행하세요.");
                }
            }
            catch (HttpRequestException) { }
            catch (TaskCanceledException) { }
            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            string engine = Path.Combine(root, "engine", "engine.exe");
            if (!File.Exists(engine) && !File.Exists(python)) throw new Exception("실행 파일만 옮기지 말고 압축 파일 전체를 풀어주세요. engine 폴더가 필요합니다.");
            var info = new System.Diagnostics.ProcessStartInfo(File.Exists(engine) ? engine : python,
                File.Exists(engine) ? "--desktop" : "\"" + Path.Combine(root, "app.py") + "\" --desktop")
            {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardError = true, RedirectStandardOutput = true
            };
            info.EnvironmentVariables["GERSANG_DESKTOP_TOKEN"] = token;
            backend = new System.Diagnostics.Process { StartInfo = info };
            backend.ErrorDataReceived += delegate(object sender, System.Diagnostics.DataReceivedEventArgs e) { Log(e.Data); };
            backend.OutputDataReceived += delegate(object sender, System.Diagnostics.DataReceivedEventArgs e) { Log(e.Data); };
            backend.Start(); backend.BeginErrorReadLine(); backend.BeginOutputReadLine();
            bool connected = false;
            for (int i = 0; i < 80; i++)
            {
                if (backend.HasExited) throw new Exception("계산 서버 시작에 실패했습니다. data/desktop.log를 확인하세요.");
                try { var response = await http.GetAsync(LocalUrl + "/api/state"); connected = response.IsSuccessStatusCode; response.Dispose(); }
                catch (HttpRequestException) { }
                if (connected) break;
                await Task.Delay(150);
            }
            if (!connected) throw new Exception("계산 서버에 연결하지 못했습니다.");
            var mainEnvironment = await CoreWebView2Environment.CreateAsync(runtimeFolder, Path.Combine(root, "data", "desktop", "calculator"));
            await calculator.EnsureCoreWebView2Async(mainEnvironment);
            calculator.CoreWebView2.Settings.IsStatusBarEnabled = false;
            calculator.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { if (!e.Uri.StartsWith(LocalUrl + "/", StringComparison.Ordinal)) e.Cancel = true; };
            calculator.CoreWebView2.Navigate(LocalUrl + "/");
            ready = true;
            if (smoke)
            {
                string value = await calculator.CoreWebView2.ExecuteScriptAsync("6 * 7");
                if (value != "42") throw new Exception("계산 화면 연결 점검에 실패했습니다.");
                Log("SMOKE: calculator ready; external Edge collector configured; no external navigation.");
            }
        }
        catch (Exception e)
        {
            Log(e.ToString());
            MessageBox.Show(e.Message, "제작으로부자되자 실행 오류", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private void Log(string line)
    {
        if (String.IsNullOrEmpty(line)) return;
        try { lock (root) { Directory.CreateDirectory(Path.Combine(root, "data")); File.AppendAllText(Path.Combine(root, "data", "desktop.log"), DateTime.Now.ToString("s") + " " + line + Environment.NewLine, Encoding.UTF8); } }
        catch (IOException) { }
    }

    private async void CloseApplication(object sender, FormClosingEventArgs e)
    {
        if (closing) return;
        e.Cancel = true;
        closing = true;
        if (backend != null && !backend.HasExited)
        {
            try
            {
                using (var content = new StringContent("{}", Encoding.UTF8, "application/json"))
                using (var response = await http.PostAsync(LocalUrl + "/api/desktop/close", content)) { }
            }
            catch (Exception) { }
            await Task.Run(delegate { if (!backend.WaitForExit(5000)) backend.Kill(); });
        }
        http.Dispose();
        Close();
    }
}
