"""How-to guide for every Capzoryx feature, in English, Banglish and Bangla.

`match` finds the feature a how-to question is about; `GUIDE[key]` holds the answer and the app page to open. The
web assistant shows an "Open …" button for the page; chat apps get the path in words.
"""
import re

HOW_WORDS = re.compile(r'\b(?:how|where|what is|what are|what does|why|can i|kivabe|kibhabe|kemne|kemone|kothay|koi|ki|'
                       r'keno|shikhao|bujhiye|explain)\b|কীভাবে|কিভাবে|কোথায়|কোথায়|কী|কি|কেন|বুঝিয়ে|বুঝিয়ে', re.I)
HOWTO = re.compile(HOW_WORDS.pattern + r'|\b(?:korbo|kori|korte|chai|nibo|diye|kaj kore)\b|[?？]|করব|চাই', re.I)
GENERIC = ('add_record', 'invoices')  # broad guides lose ties to specific ones such as voice or khata

# key: (keywords, page, {lang: answer})
GUIDE = {
    'add_record': (('add', 'record', 'entry', 'entri', 'likhbo', 'jog', 'expense', 'income', 'এন্ট্রি', 'যোগ'), 'Data', {
        'en': 'Open Add records. Type it like a message ("Received 500 from Rahim"), take a photo of the bill, or use the form; check the draft and press Save.',
        'bl': 'Add records page-e jan. Message-er moto likhun ("Rahim theke 500 pelam"), biller chhobi tulun, ba form puron korun; draft dekhe Save chapun.',
        'bn': 'Add records পেজে যান। মেসেজের মতো লিখুন ("রহিম থেকে ৫০০ পেলাম"), বিলের ছবি তুলুন বা ফর্ম পূরণ করুন; ড্রাফট দেখে Save চাপুন।'}),
    'scan_bill': (('photo', 'chobi', 'chhobi', 'scan', 'camera', 'receipt', 'ছবি', 'স্ক্যান'), 'Data', {
        'en': 'In Add records choose Scan a bill and take a clear photo. Capzoryx reads the supplier, number, dates and total on your device; check them before saving.',
        'bl': 'Add records-e Scan a bill chepe porishkar chhobi tulun. Supplier, number, tarikh ar mot taka phone-ei pora hoy; save-er age mile nin.',
        'bn': 'Add records-এ Scan a bill চেপে পরিষ্কার ছবি তুলুন। সাপ্লায়ার, নম্বর, তারিখ আর মোট টাকা ফোনেই পড়া হয়; সেভের আগে মিলিয়ে নিন।'}),
    'khata': (('khata', 'notebook', 'khatar', 'page', 'pata', 'খাতা'), 'Data', {
        'en': 'In Add records choose Photograph a khata page. Each row is read and shown for review; confirm the rows you want to save.',
        'bl': 'Add records-e "Photograph a khata page" chepe khatar pata-r chhobi tulun. Proti line dekhabe; jegula thik, segula save korun.',
        'bn': 'Add records-এ "Photograph a khata page" চেপে খাতার পাতার ছবি তুলুন। প্রতিটি লাইন দেখাবে; যেগুলো ঠিক, সেগুলো সেভ করুন।'}),
    'voice': (('voice', 'mic', 'bole', 'kotha', 'dictate', 'speech', 'ভয়েস', 'বলে'), 'Data', {
        'en': 'In Add records press the microphone and say the payment, for example "Rahman ke 5000 dilam". You can download an offline speech pack for Bangla or English.',
        'bl': 'Add records-e microphone chepe bolun, jemon "Rahman ke 5000 dilam". Net chhara bolar jonno Bangla ba English offline speech pack namate paren.',
        'bn': 'Add records-এ মাইক্রোফোন চেপে বলুন, যেমন "রহমানকে ৫০০০ দিলাম"। নেট ছাড়া বলার জন্য বাংলা বা ইংরেজি অফলাইন স্পিচ প্যাক নামাতে পারেন।'}),
    'invoices': (('invoice', 'invoices', 'bill', 'bills', 'due', 'supplier', 'বিল', 'ইনভয়েস'), 'Invoices', {
        'en': 'Invoices lists every unpaid supplier bill with its due date. Add a bill in Add records; to pay one, open it and press Prepare payment for approval.',
        'bl': 'Invoices page-e shob baki supplier bill ar tar tarikh ache. Notun bill Add records theke jog korun; dite chaile bill khule Prepare payment chapun.',
        'bn': 'Invoices পেজে সব বাকি সাপ্লায়ার বিল আর তার তারিখ আছে। নতুন বিল Add records থেকে যোগ করুন; দিতে চাইলে বিল খুলে Prepare payment চাপুন।'}),
    'approval': (('approve', 'approval', 'approver', 'onumodon', 'অনুমোদন'), 'Approvals', {
        'en': 'Open Approvals and choose Review and decide. If you work alone you approve your own payments after ticking that you checked them; once you invite an approver (Settings → Sharing), only they can approve. Approving records the payment; it never sends money.',
        'bl': 'Approvals-e giye Review and decide chapun. Apni eka kaj korle nije check kore tick diye approve korte parben; Settings → Sharing theke approver invite korle tokhon shudhu tini approve korben. Approve korle shudhu hisab-e lekha hoy, taka jay na.',
        'bn': 'Approvals-এ গিয়ে Review and decide চাপুন। আপনি একা কাজ করলে নিজে যাচাই করে টিক দিয়ে অনুমোদন করতে পারবেন; Settings → Sharing থেকে approver আমন্ত্রণ করলে তখন শুধু তিনিই অনুমোদন করবেন। অনুমোদনে শুধু হিসাবে লেখা হয়, টাকা যায় না।'}),
    'loans': (('loan', 'loans', 'rin', 'murabaha', 'lender', 'interest', 'sud', 'ঋণ', 'লোন', 'সুদ'), 'Financing', {
        'en': 'Open Loans and enter each offer you received. Capzoryx shows the true yearly cost with fees and whether your cash flow can carry the instalments.',
        'bl': 'Loans page-e pawa offer-gula likhun. Fee shoho ashol bochhorik khoroch ar apnar cash-flow kisti tante parbe kina, Capzoryx dekhabe.',
        'bn': 'Loans পেজে পাওয়া অফারগুলো লিখুন। ফি সহ আসল বার্ষিক খরচ আর আপনার নগদ প্রবাহ কিস্তি টানতে পারবে কি না, Capzoryx দেখাবে।'}),
    'passport': (('passport', 'qr', 'verify', 'পাসপোর্ট'), 'Audit', {
        'en': 'A cash-flow passport is a signed summary of your cash flow that a lender can verify by QR. Issue it from Activity; it expires and you can revoke it anytime.',
        'bl': 'Cash-flow passport holo apnar nogod probaher ekta signed shar-shonkhep, ja lender QR diye jachai korte pare. Activity theke banan; meyad ache, jokhon khushi batil korte paren.',
        'bn': 'ক্যাশ-ফ্লো পাসপোর্ট হলো আপনার নগদ প্রবাহের একটি স্বাক্ষরিত সারসংক্ষেপ, যা ঋণদাতা QR দিয়ে যাচাই করতে পারেন। Activity থেকে বানান; মেয়াদ আছে, যখন খুশি বাতিল করতে পারেন।'}),
    'collection': (('collection', 'collect', 'aday', 'tola', 'gap', 'shortfall', 'ghatti', 'আদায়', 'ঘাটতি'), 'Cash flow', {
        'en': 'In Cash flow, Plan early collections compares up to three customer payments to close a cash gap. Accepting a plan creates collection tasks.',
        'bl': 'Cash flow page-e "Plan early collections" cash-er ghatti mitate tin-ta porjonto customer payment age tulte tulona kore. Plan nile collection task toiri hoy.',
        'bn': 'Cash flow পেজে "Plan early collections" নগদ ঘাটতি মেটাতে তিনটি পর্যন্ত গ্রাহকের পেমেন্ট আগে তোলার তুলনা করে। পরিকল্পনা নিলে আদায়ের কাজ তৈরি হয়।'}),
    'reminder': (('reminder', 'remind', 'tagada', 'tagid', 'তাগাদা', 'রিমাইন্ডার'), 'Invoices', {
        'en': 'Open the customer due and press Draft reminder. Capzoryx writes a polite message in the customer\'s language; you send it yourself by WhatsApp or SMS.',
        'bl': 'Customer-er baki khule Draft reminder chapun. Capzoryx bhodro bhashay message likhe dey; apni nije WhatsApp ba SMS-e pathan.',
        'bn': 'গ্রাহকের বাকি খুলে Draft reminder চাপুন। Capzoryx ভদ্র ভাষায় মেসেজ লিখে দেয়; আপনি নিজে WhatsApp বা SMS-এ পাঠান।'}),
    'payroll': (('payroll', 'salary', 'salaries', 'beton', 'bonus', 'bonas', 'employee', 'staff', 'payslip', 'karmochari', 'বেতন', 'বোনাস', 'কর্মচারী', 'স্টাফ'), 'Payroll', {
        'en': 'Open Payroll → Add employee for each person. Pay salaries pays everyone due for the month, and Give a bonus pays selected people. Press Invite link next to an employee and send it; they choose their own username and password and see only their own payslips.',
        'bl': 'Payroll → Add employee diye protiti karmi jog korun. Pay salaries chaple oi maser sobar beton hoy, Give a bonus diye bachai kora lokder bonus din. Karmir pashe Invite link chepe link-ta pathan; uni nijer username o password diye shudhu nijer payslip dekhben.',
        'bn': 'Payroll → Add employee দিয়ে প্রত্যেক কর্মী যোগ করুন। Pay salaries চাপলে ওই মাসের সবার বেতন হয়, Give a bonus দিয়ে বাছাই করা কর্মীদের বোনাস দিন। কর্মীর পাশে Invite link চেপে লিংকটি পাঠান; তিনি নিজের ইউজারনেম ও পাসওয়ার্ড দিয়ে শুধু নিজের পে-স্লিপ দেখবেন।'}),
    'telegram': (('telegram', 'bot', 'টেলিগ্রাম'), 'Settings', {
        'en': 'Go to Settings → Telegram → Connect Telegram, then press Start in the bot chat. You can then ask about your books and get bill reminders there.',
        'bl': 'Settings → Telegram → Connect Telegram chepe bot chat-e Start chapun. Tarpor oikhanei hisab jiggesh korte parben ar bill-er reminder paben.',
        'bn': 'Settings → Telegram → Connect Telegram চেপে বট চ্যাটে Start চাপুন। তারপর সেখানেই হিসাব জিজ্ঞেস করতে পারবেন আর বিলের রিমাইন্ডার পাবেন।'}),
    'sms': (('sms', 'bkash', 'nagad', 'rocket', 'forward', 'forwarding', 'বিকাশ'), 'Settings', {
        'en': 'Settings → Payment SMS → Set up SMS forwarding gives a private link. Add it to a free SMS forwarder app on your Android phone; each payment SMS becomes a draft you save or skip.',
        'bl': 'Settings → Payment SMS → Set up SMS forwarding ekta gopon link dey. Android phone-er free SMS forwarder app-e link-ta din; proti payment SMS ekta draft hoy, apni Save ba Skip korben.',
        'bn': 'Settings → Payment SMS → Set up SMS forwarding একটি গোপন লিংক দেয়। অ্যান্ড্রয়েড ফোনের ফ্রি SMS ফরোয়ার্ডার অ্যাপে লিংকটি দিন; প্রতিটি পেমেন্ট SMS একটি ড্রাফট হয়, আপনি Save বা Skip করবেন।'}),
    'pdf': (('pdf', 'report', 'print', 'download', 'রিপোর্ট'), None, {
        'en': 'Press the download icon at the top of any page to get a PDF report with your balance, bills, cash activity and payment decisions.',
        'bl': 'Je kono page-er upore download icon chapun; balance, bill, cash activity ar payment shiddhanto shoho PDF report pabein.',
        'bn': 'যেকোনো পেজের উপরে ডাউনলোড আইকন চাপুন; ব্যালেন্স, বিল, নগদ লেনদেন আর পেমেন্টের সিদ্ধান্তসহ PDF রিপোর্ট পাবেন।'}),
    'theme': (('dark', 'light', 'theme', 'mode', 'অন্ধকার', 'ডার্ক'), None, {
        'en': 'Use the Dark / Light button at the top of the page to switch the theme.',
        'bl': 'Page-er upore Dark / Light button chepe theme bodlan.',
        'bn': 'পেজের উপরে Dark / Light বোতাম চেপে থিম বদলান।'}),
    'language': (('language', 'bhasha', 'bangla', 'english', 'ভাষা'), 'Settings', {
        'en': 'Choose a language from the menu in the side bar or in Settings → Language. Capzoryx supports 37 languages; your records stay as you typed them.',
        'bl': 'Side bar-er menu ba Settings → Language theke bhasha bachun. 37-ta bhasha ache; apnar entry jemon likhechen temni thakbe.',
        'bn': 'সাইডবারের মেনু বা Settings → Language থেকে ভাষা বাছুন। ৩৭টি ভাষা আছে; আপনার এন্ট্রি যেমন লিখেছেন তেমনই থাকবে।'}),
    'team': (('team', 'member', 'invite', 'share', 'sharing', 'staff', 'karmochari', 'সদস্য', 'শেয়ার'), 'Settings', {
        'en': 'Settings → Sharing → invite a person as approver, bookkeeper or viewer. Each role sees and does only what it should; you can remove access anytime.',
        'bl': 'Settings → Sharing theke kauke approver, bookkeeper ba viewer hishebe invite korun. Proti role shudhu nijer kaj korte pare; jokhon khushi access batil korte paren.',
        'bn': 'Settings → Sharing থেকে কাউকে approver, bookkeeper বা viewer হিসেবে আমন্ত্রণ জানান। প্রতিটি ভূমিকা শুধু নিজের কাজ করতে পারে; যখন খুশি প্রবেশাধিকার বাতিল করতে পারেন।'}),
    'offline': (('offline', 'internet', 'net', 'অফলাইন', 'ইন্টারনেট'), 'Data', {
        'en': 'Yes. Install Capzoryx, then entries made without internet are saved on the phone and sync automatically when you reconnect. Balances update after syncing.',
        'bl': 'Hya. Capzoryx install korle net chhara entry phone-e save thake, net firle nije sync hoy. Sync-er por balance update hoy.',
        'bn': 'হ্যাঁ। Capzoryx ইনস্টল করলে নেট ছাড়া এন্ট্রি ফোনে সেভ থাকে, নেট ফিরলে নিজে সিঙ্ক হয়। সিঙ্কের পর ব্যালেন্স আপডেট হয়।'}),
    'password': (('password', 'forgot', 'bhule', 'login', 'two', 'step', '2fa', 'পাসওয়ার্ড', 'ভুলে'), 'Settings', {
        'en': 'Change it in Settings → Password & 2-step, where you can also turn on two-step sign-in. If you forgot it, choose Forgot password on the sign-in page.',
        'bl': 'Settings → Password & 2-step theke bodlan, oikhane 2-step sign-in-o chalu korte paren. Bhule gele sign-in page-e Forgot password chapun.',
        'bn': 'Settings → Password & 2-step থেকে বদলান, সেখানে দুই-ধাপের সাইন-ইনও চালু করতে পারেন। ভুলে গেলে সাইন-ইন পেজে Forgot password চাপুন।'}),
    'quick_jump': (('ctrl', 'shortcut', 'search', 'khuji', 'jump', 'খুঁজব'), None, {
        'en': 'Press Ctrl+K (⌘+K on Mac) or the search icon, type a few letters of a page or action and press Enter.',
        'bl': 'Ctrl+K (Mac-e ⌘+K) ba search icon chapun, page-er naam-er kichu okkhor likhe Enter din.',
        'bn': 'Ctrl+K (ম্যাকে ⌘+K) বা সার্চ আইকন চাপুন, পেজের নামের কয়েকটি অক্ষর লিখে Enter দিন।'}),
    'backup': (('backup', 'export', 'clear', 'delete', 'reset', 'mucha', 'ব্যাকআপ', 'মুছ'), 'Settings', {
        'en': 'Settings → Data & workspaces lets you download a full backup and clear a workspace; a cleared workspace can be restored for a limited time.',
        'bl': 'Settings → Data & workspaces theke puro backup namate paren ar workspace clear korte paren; clear korle kichu shomoy porjonto ferot ana jay.',
        'bn': 'Settings → Data & workspaces থেকে পুরো ব্যাকআপ নামাতে পারেন আর ওয়ার্কস্পেস মুছতে পারেন; মুছলে কিছু সময় পর্যন্ত ফেরত আনা যায়।'}),
    'currency': (('currency', 'dollar', 'usd', 'mudra', 'মুদ্রা', 'ডলার'), None, {
        'en': 'Pick any currency in "Show amounts in" at the top of the Overview; amounts are converted at today\'s rate for display while your books stay in their own currency.',
        'bl': 'Overview-er upore "Show amounts in" theke je kono mudra bachun; ajker rate-e dekhabe, kintu hisab nijer mudray-i thakbe.',
        'bn': 'Overview-এর উপরে "Show amounts in" থেকে যেকোনো মুদ্রা বাছুন; আজকের রেটে দেখাবে, কিন্তু হিসাব নিজের মুদ্রাতেই থাকবে।'}),
}
PAGE_LABEL = {'Data': 'Add records', 'Invoices': 'Invoices', 'Cash flow': 'Cash flow', 'Financing': 'Loans',
              'Approvals': 'Approvals', 'Payroll': 'Payroll', 'Audit': 'Activity', 'Settings': 'Settings'}


def match(normalized, tokens, text):
    """The guide entry a how-to question is about, or None."""
    best, score = None, 0
    for key in sorted(GUIDE, key=lambda k: k in GENERIC):
        hits = sum(1 for k in GUIDE[key][0] if k in tokens or (len(k) > 4 and any(t.startswith(k) for t in tokens)))
        if hits > score:
            best, score = key, hits
    # A how-to cue and one feature word, or two feature words on their own ("khata er page er chobi").
    return best if score >= 2 or (score and HOWTO.search(text)) else None
