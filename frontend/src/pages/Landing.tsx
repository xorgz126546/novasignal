import { Link } from "react-router-dom";
import { Logo, RiskNote } from "../components/ui";
import { useFetch } from "../lib/useFetch";

const FEATURES = [
  ["Spot only, by design", "Binance Spot. No futures, no margin, no leverage, no automatic execution and never any withdrawal permission."],
  ["Few signals, on purpose", "At most 3 qualified signals per day. When nothing passes every agent, nothing is published — zero is a valid day."],
  ["One target, one stop", "Every signal has exactly one entry, one take profit and one stop loss, rounded to exchange tick size and net of fees."],
  ["Transparent confidence", "Confidence is a documented, weighted sum of agent evidence scores — never a fabricated win probability."],
  ["Telegram + dashboard", "Get signals in Telegram, report ENTERED / SKIPPED / CLOSED with your real prices, and see everything in the dashboard."],
  ["Honest performance", "Theoretical, paper-traded and your actual results are tracked separately — never blended into one flattering number."],
];

const FAQ = [
  ["Does CRYPTONEX AI trade for me?", "No. It never places orders. You read the signal and decide whether to trade manually on Binance."],
  ["Do you guarantee profits or a win rate?", "No. Nobody can. The system targets 3–5% moves when conditions justify it, and frequently publishes nothing at all."],
  ["What does the confidence score mean?", "It's a weighted evidence score from six agents (0–100). It is not the probability that a trade wins."],
  ["Do I need to connect my Binance account?", "No. CRYPTONEX AI uses Binance's public market data only. No API keys from you are needed."],
  ["Where is the track record?", "Performance is published only after the 30-day validation program completes, including fees and slippage. Until then, there are no results to show."],
];

export default function Landing() {
  const { data: m } = useFetch<any>("/api/public/methodology");
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-graphite-700/70 bg-ink/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
          <Logo small />
          <nav className="flex items-center gap-2 sm:gap-4 text-sm">
            <a href="#agents" className="hidden sm:inline text-zinc-400 hover:text-white">Agents</a>
            <a href="#pricing" className="hidden sm:inline text-zinc-400 hover:text-white">Pricing</a>
            <a href="#faq" className="hidden sm:inline text-zinc-400 hover:text-white">FAQ</a>
            <Link to="/login" className="text-zinc-300 hover:text-white">Sign in</Link>
            <Link to="/register" className="btn-primary py-1.5">Get started</Link>
          </nav>
        </div>
      </header>

      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,#ff2a3d22,transparent_55%)]" />
        <div className="relative max-w-6xl mx-auto px-4 pt-20 pb-24 text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-graphite-500 px-3 py-1 text-xs text-zinc-400 mb-6">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" /> 11 specialized agents · Binance Spot
          </div>
          <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight leading-[1.05]">
            Fewer signals.<br /><span className="text-accent">Better-reasoned</span> ones.
          </h1>
          <p className="mt-6 text-lg text-zinc-400 max-w-2xl mx-auto">
            CRYPTONEX AI scans Binance Spot markets with eleven independent agents and publishes at most three fully-reasoned BUY
            signals a day — each with one entry, one take profit and one stop loss. Or none, when the market offers nothing worth taking.
          </p>
          <div className="mt-9 flex gap-3 justify-center flex-wrap">
            <Link to="/register" className="btn-primary px-6 py-3 text-base">Join the private test</Link>
            <a href="#how" className="btn-ghost px-6 py-3 text-base">How it works</a>
          </div>
          <div className="mt-14 mx-auto max-w-md card p-5 text-left font-mono text-sm shadow-[0_0_60px_-20px_#ff2a3d]">
            <div className="text-accent font-bold">CRYPTONEX AI · NEW SPOT SIGNAL</div>
            <div className="text-[11px] text-zinc-500 mb-3">Format example — illustrative levels, not a real signal</div>
            <div className="grid grid-cols-2 gap-y-1">
              <span className="text-zinc-500">Pair</span><span>BTC/USDT</span>
              <span className="text-zinc-500">Action</span><span>BUY</span>
              <span className="text-zinc-500">Entry</span><span>90,000</span>
              <span className="text-zinc-500">Take Profit</span><span className="text-up">93,600</span>
              <span className="text-zinc-500">Stop Loss</span><span className="text-down">89,100</span>
              <span className="text-zinc-500">Expected move</span><span>+4%</span>
              <span className="text-zinc-500">Risk/Reward</span><span>1:4</span>
              <span className="text-zinc-500">Status</span><span>WAITING FOR USER</span>
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="max-w-6xl mx-auto px-4 py-16">
        <h2 className="text-2xl sm:text-3xl font-bold">Built to say “no” most of the time</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-8">
          {FEATURES.map(([t, d]) => (
            <div key={t} className="card card-pad"><div className="font-semibold">{t}</div><p className="text-sm text-zinc-400 mt-2">{d}</p></div>
          ))}
        </div>
      </section>

      <section id="agents" className="max-w-6xl mx-auto px-4 py-16 border-t border-graphite-700">
        <h2 className="text-2xl sm:text-3xl font-bold">The 11-agent architecture</h2>
        <p className="text-zinc-400 mt-2 max-w-2xl">Each agent has a defined responsibility, typed input/output schema, validation, logging and its own tests. Price calculations are deterministic; language models are only used, optionally, to explain an approved signal in plain English.</p>
        <ol className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-8">
          {(m?.agents ?? []).map((a: any) => (
            <li key={a.number} className="card card-pad">
              <div className="num text-xs text-accent">AGENT {String(a.number).padStart(2, "0")}</div>
              <div className="font-semibold mt-1">{a.name}</div>
              <p className="text-xs text-zinc-400 mt-1.5">{a.responsibility}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="max-w-6xl mx-auto px-4 py-16 border-t border-graphite-700">
        <h2 className="text-2xl sm:text-3xl font-bold">Performance methodology</h2>
        <div className="grid lg:grid-cols-3 gap-4 mt-8">
          {[["Theoretical", "Did price reach the take profit or the stop loss first, using 1-minute candles? If both in one candle, we count the stop."],
            ["Paper", "The same signals with 0.10% fees per side, slippage on entry and stops, and execution delay."],
            ["Actual", "Only trades users confirmed, with their real fill prices and fees."]].map(([t, d]) => (
            <div key={t} className="card card-pad"><div className="font-semibold">{t}</div><p className="text-sm text-zinc-400 mt-2">{d}</p></div>
          ))}
        </div>
        <div className="card card-pad mt-4 text-sm text-zinc-300">
          <span className="font-semibold">Track record status: </span>
          {m?.track_record?.published ? "published" : "not yet published."} {m?.track_record?.note}
        </div>
      </section>

      <section id="pricing" className="max-w-6xl mx-auto px-4 py-16 border-t border-graphite-700">
        <h2 className="text-2xl sm:text-3xl font-bold">Pricing</h2>
        <div className="card p-8 mt-8 max-w-md">
          <div className="label">CRYPTONEX AI Pro</div>
          <div className="mt-2"><span className="text-5xl font-extrabold">${m?.pricing?.usd_per_month ?? 25}</span><span className="text-zinc-500"> / month</span></div>
          <ul className="mt-6 space-y-2 text-sm text-zinc-300">
            <li>✓ All signals in dashboard and Telegram</li><li>✓ Agent analysis for every signal</li><li>✓ Trade journal & analytics</li><li>✓ Validation reports</li>
          </ul>
          <p className="text-xs text-zinc-500 mt-6">{m?.pricing?.billing_active ? "Billed monthly. Cancel anytime." : "Paid plans open after the 30-day validation and payment/legal checks. Joining now is free during the private test."}</p>
          <Link to="/register" className="btn-primary w-full mt-4">Create account</Link>
        </div>
      </section>

      <section id="faq" className="max-w-6xl mx-auto px-4 py-16 border-t border-graphite-700">
        <h2 className="text-2xl sm:text-3xl font-bold">FAQ</h2>
        <div className="mt-8 space-y-3 max-w-3xl">
          {FAQ.map(([q, a]) => (
            <details key={q} className="card card-pad group"><summary className="cursor-pointer font-medium list-none flex justify-between">{q}<span className="text-zinc-500 group-open:rotate-45 transition">+</span></summary><p className="text-sm text-zinc-400 mt-3">{a}</p></details>
          ))}
        </div>
      </section>

      <section id="risk" className="max-w-6xl mx-auto px-4 py-16 border-t border-graphite-700">
        <h2 className="text-xl font-bold">Risk disclosure</h2>
        <div className="mt-4 text-sm text-zinc-400 space-y-3 max-w-3xl">
          <p>Cryptocurrency trading involves substantial risk of loss. Prices can move sharply and you can lose part or all of the capital you trade. CRYPTONEX AI provides analytical information only; it is not investment, financial, legal or tax advice, and it is not a registered investment adviser.</p>
          <p>No signal, score or past/simulated result guarantees any future result. Simulated (theoretical and paper) results have inherent limitations and do not reflect actual trading. You alone decide whether to trade and you are responsible for your decisions. Check that crypto trading is legal for you where you live.</p>
        </div>
        <div className="mt-10 card p-8 text-center">
          <h3 className="text-2xl font-bold">Test it with your own eyes.</h3>
          <p className="text-zinc-400 mt-2">Join the private test. Every signal and every result is visible to you.</p>
          <Link to="/register" className="btn-primary mt-6 px-6 py-3">Create free account</Link>
        </div>
      </section>

      <footer className="border-t border-graphite-700 py-8">
        <div className="max-w-6xl mx-auto px-4 flex flex-col sm:flex-row gap-4 justify-between items-start">
          <Logo small />
          <div className="max-w-xl"><RiskNote /></div>
        </div>
      </footer>
    </div>
  );
}
