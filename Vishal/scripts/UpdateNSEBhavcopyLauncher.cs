using System;
using System.Diagnostics;
using System.IO;

class UpdateNSEBhavcopyLauncher
{
    static int Main()
    {
        string root = @"C:\Users\khadk\OneDrive\Documents\Vishal";
        string python = @"C:\Users\khadk\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe";
        string script = Path.Combine(root, "scripts", "update_nse_fo_last60.py");

        Console.Title = "Update Vishal F&O Bhavcopy";
        Console.WriteLine("Updating Vishal F&O bhavcopy workbook for the last 60 days...");
        Console.WriteLine();

        if (!File.Exists(python))
        {
            Console.WriteLine("Python runtime was not found:");
            Console.WriteLine(python);
            Console.WriteLine();
            Console.WriteLine("Press any key to close.");
            Console.ReadKey();
            return 1;
        }

        if (!File.Exists(script))
        {
            Console.WriteLine("Updater script was not found:");
            Console.WriteLine(script);
            Console.WriteLine();
            Console.WriteLine("Press any key to close.");
            Console.ReadKey();
            return 1;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            Arguments = "\"" + script + "\"",
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
        };

        using (var process = Process.Start(startInfo))
        {
            process.WaitForExit();
            Console.WriteLine();
            if (process.ExitCode == 0)
            {
                Console.WriteLine("Finished successfully.");
                Console.WriteLine(Path.Combine(root, "outputs", "vishal_last_60_days", "vishal_last_60_days_combined.xlsx"));
            }
            else
            {
                Console.WriteLine("Update failed. Check:");
                Console.WriteLine(Path.Combine(root, "outputs", "vishal_last_60_days", "last_run.log"));
            }
            Console.WriteLine();
            Console.WriteLine("Press any key to close.");
            Console.ReadKey();
            return process.ExitCode;
        }
    }
}
