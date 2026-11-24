// Chrome native-messaging host for Violet Docs Caret.
//
// This console executable is intentionally silent: stdout is reserved for the
// native-messaging protocol, and it writes no diagnostics or document data.
// It accepts a fixed geometry-only schema from the extension, reads a rotating
// per-launch bearer from Violet's current-user data directory, and forwards
// the normalized packet over an authenticated loopback connection.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal static class VioletCaretHost
{
    private const int BridgeVersion = 1;
    private const int MaxNativeMessageBytes = 16 * 1024;
    private const int ConnectTimeoutMilliseconds = 250;
    private const string AllowedExtensionOrigin = "chrome-extension://ljjgdbghiehmhfladhlehonfomhlnoch/";
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private static readonly Regex SessionPattern = new Regex("^[A-Za-z0-9_-]{8,96}$", RegexOptions.CultureInvariant);
    private static TcpClient client;
    private static StreamWriter writer;

    private sealed class Descriptor
    {
        internal int Port;
        internal string Token;
    }

    private static void Main(string[] arguments)
    {
        // Chrome supplies the caller origin as the first host argument. The
        // manifest allowlist is the primary boundary; checking it again keeps
        // this host fail-closed if it is launched through another mechanism.
        if (arguments == null || arguments.Length == 0 ||
            !String.Equals(arguments[0], AllowedExtensionOrigin, StringComparison.Ordinal))
        {
            return;
        }
        try
        {
            using (Stream input = Console.OpenStandardInput())
            {
                while (true)
                {
                    string raw = ReadNativeMessage(input);
                    if (raw == null)
                    {
                        break;
                    }
                    Dictionary<string, object> parsed = null;
                    try
                    {
                        parsed = Json.DeserializeObject(raw) as Dictionary<string, object>;
                    }
                    catch (ArgumentException)
                    {
                        continue;
                    }
                    Dictionary<string, object> safe = Sanitize(parsed);
                    if (safe != null)
                    {
                        Forward(safe);
                    }
                }
            }
        }
        catch (IOException)
        {
            // Chrome closing the native pipe is a normal shutdown path.
        }
        finally
        {
            CloseConnection();
        }
    }

    private static string ReadNativeMessage(Stream input)
    {
        byte[] header = ReadExactly(input, 4);
        if (header == null)
        {
            return null;
        }
        uint length = BitConverter.ToUInt32(header, 0);
        if (length == 0 || length > MaxNativeMessageBytes)
        {
            return null;
        }
        byte[] body = ReadExactly(input, (int)length);
        return body == null ? null : Encoding.UTF8.GetString(body);
    }

    private static byte[] ReadExactly(Stream input, int count)
    {
        byte[] buffer = new byte[count];
        int offset = 0;
        while (offset < count)
        {
            int read = input.Read(buffer, offset, count - offset);
            if (read == 0)
            {
                return null;
            }
            offset += read;
        }
        return buffer;
    }

    private static Dictionary<string, object> Sanitize(Dictionary<string, object> message)
    {
        if (message == null || !IsVersion(message))
        {
            return null;
        }
        string type = GetString(message, "type");
        if (String.Equals(type, "clear", StringComparison.Ordinal))
        {
            return new Dictionary<string, object>
            {
                { "type", "clear" },
                { "version", BridgeVersion },
            };
        }
        if (!String.Equals(type, "caret", StringComparison.Ordinal) || !IsTrue(message, "focused") || !IsTrue(message, "visible"))
        {
            return null;
        }
        string session = GetString(message, "session");
        long sequence;
        double left, top, right, bottom, viewportWidth, viewportHeight;
        if (session == null || !SessionPattern.IsMatch(session) ||
            !TryWholeNumber(message, "sequence", out sequence) || sequence < 0 || sequence > 9007199254740991L ||
            !TryFiniteNumber(message, "left", out left) ||
            !TryFiniteNumber(message, "top", out top) ||
            !TryFiniteNumber(message, "right", out right) ||
            !TryFiniteNumber(message, "bottom", out bottom) ||
            !TryFiniteNumber(message, "viewport_width", out viewportWidth) ||
            !TryFiniteNumber(message, "viewport_height", out viewportHeight))
        {
            return null;
        }
        if (viewportWidth < 160 || viewportWidth > 32768 || viewportHeight < 100 || viewportHeight > 32768 ||
            left < 0 || top < 0 || right < left || bottom < top || right > viewportWidth + 8 || bottom > viewportHeight + 8 ||
            right - left > 64 || bottom - top < 1 || bottom - top > 320)
        {
            return null;
        }
        // Construct a new message rather than proxying the extension payload,
        // so a future extension bug cannot send document content through this
        // privileged local bridge.
        return new Dictionary<string, object>
        {
            { "type", "caret" },
            { "version", BridgeVersion },
            { "session", session },
            { "sequence", sequence },
            { "left", left },
            { "top", top },
            { "right", right },
            { "bottom", bottom },
            { "viewport_width", viewportWidth },
            { "viewport_height", viewportHeight },
            { "focused", true },
            { "visible", true },
        };
    }

    private static bool IsVersion(Dictionary<string, object> message)
    {
        long value;
        return TryWholeNumber(message, "version", out value) && value == BridgeVersion;
    }

    private static bool IsTrue(Dictionary<string, object> message, string name)
    {
        object value;
        return message.TryGetValue(name, out value) && value is bool && (bool)value;
    }

    private static string GetString(Dictionary<string, object> message, string name)
    {
        object value;
        return message.TryGetValue(name, out value) ? value as string : null;
    }

    private static bool TryWholeNumber(Dictionary<string, object> message, string name, out long result)
    {
        result = 0;
        object value;
        if (!message.TryGetValue(name, out value) || value is bool || value == null)
        {
            return false;
        }
        try
        {
            decimal numeric = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
            if (numeric != Decimal.Truncate(numeric) || numeric < Int64.MinValue || numeric > Int64.MaxValue)
            {
                return false;
            }
            result = Decimal.ToInt64(numeric);
            return true;
        }
        catch (FormatException) { return false; }
        catch (InvalidCastException) { return false; }
        catch (OverflowException) { return false; }
    }

    private static bool TryFiniteNumber(Dictionary<string, object> message, string name, out double result)
    {
        result = 0;
        object value;
        if (!message.TryGetValue(name, out value) || value is bool || value == null)
        {
            return false;
        }
        try
        {
            double numeric = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (Double.IsNaN(numeric) || Double.IsInfinity(numeric))
            {
                return false;
            }
            result = numeric;
            return true;
        }
        catch (FormatException) { return false; }
        catch (InvalidCastException) { return false; }
        catch (OverflowException) { return false; }
    }

    private static void Forward(Dictionary<string, object> payload)
    {
        for (int attempt = 0; attempt < 2; attempt++)
        {
            if (!EnsureConnection())
            {
                return;
            }
            try
            {
                writer.WriteLine(Json.Serialize(payload));
                writer.Flush();
                return;
            }
            catch (IOException)
            {
                CloseConnection();
            }
            catch (ObjectDisposedException)
            {
                CloseConnection();
            }
        }
    }

    private static bool EnsureConnection()
    {
        if (writer != null && client != null && client.Connected)
        {
            return true;
        }
        CloseConnection();
        Descriptor descriptor = ReadDescriptor();
        if (descriptor == null)
        {
            return false;
        }
        TcpClient nextClient = new TcpClient();
        try
        {
            IAsyncResult connect = nextClient.BeginConnect("127.0.0.1", descriptor.Port, null, null);
            if (!connect.AsyncWaitHandle.WaitOne(ConnectTimeoutMilliseconds))
            {
                nextClient.Close();
                return false;
            }
            nextClient.EndConnect(connect);
            NetworkStream stream = nextClient.GetStream();
            stream.WriteTimeout = ConnectTimeoutMilliseconds;
            StreamWriter nextWriter = new StreamWriter(stream, new UTF8Encoding(false));
            nextWriter.NewLine = "\n";
            nextWriter.AutoFlush = true;
            nextWriter.WriteLine(Json.Serialize(new Dictionary<string, object>
            {
                { "type", "hello" },
                { "version", BridgeVersion },
                { "token", descriptor.Token },
            }));
            client = nextClient;
            writer = nextWriter;
            return true;
        }
        catch (IOException)
        {
            nextClient.Close();
            return false;
        }
        catch (SocketException)
        {
            nextClient.Close();
            return false;
        }
    }

    private static Descriptor ReadDescriptor()
    {
        string path = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Violet",
            "docs-caret-bridge.json");
        try
        {
            Dictionary<string, object> descriptor = Json.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>;
            long version, port;
            if (descriptor == null)
            {
                return null;
            }
            string host = GetString(descriptor, "host");
            string token = GetString(descriptor, "token");
            if (!TryWholeNumber(descriptor, "version", out version) || version != BridgeVersion ||
                !TryWholeNumber(descriptor, "port", out port) || port < 1 || port > 65535 ||
                !String.Equals(host, "127.0.0.1", StringComparison.Ordinal) ||
                String.IsNullOrEmpty(token) || token.Length > 256)
            {
                return null;
            }
            return new Descriptor { Port = (int)port, Token = token };
        }
        catch (IOException) { return null; }
        catch (ArgumentException) { return null; }
        catch (InvalidOperationException) { return null; }
    }

    private static void CloseConnection()
    {
        StreamWriter oldWriter = writer;
        TcpClient oldClient = client;
        writer = null;
        client = null;
        if (oldWriter != null)
        {
            try { oldWriter.Dispose(); } catch (IOException) { }
        }
        if (oldClient != null)
        {
            try { oldClient.Close(); } catch (SocketException) { }
        }
    }
}
