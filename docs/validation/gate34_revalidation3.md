# Gate 3/4 revalidation 3 (held-out) - HEAD 560ecb6

Verdict: FAIL (6 plain-language bypasses; ~62% false rejection)

V1: wrong drafts 64, caught 58, bypass 6; genuine 55, rejected 34 (62%). Cases: h3_cases.json, results h3_results.json. Real DB rule chunks, real guard entry code_checks, topic_rules set as retrieve sets it.

## Bypasses
- W027 [en] Regenerate the file in SIF v2 and upload. R-UAE-WPS-2026-07 took effect on 15 July 2026, but v2 is still what the portal accepts. If E104 persists contact us.
- W031 [en] The SIF layout is v2 as of 15 July 2026 (R-UAE-WPS-2026-07). No new column is required.
- W042 [hinglish] Aap SIF v2 layout use karein, usme chaudah columns hote hain. R-UAE-WPS-2026-07 effective 15 July 2026.
- W115 [hinglish] SIF file mein chaudah columns hote hain aur layout v2 hi hai (R-UAE-WPS-2026-07, 15 July 2026 se). Re-upload karein.
- W118 [ar] ما زال التنسيق المعتمد هو v2 (R-UAE-WPS-2026-07، 2026-07-15) ولا يلزم تغيير شيء. أعيدوا الرفع.
- W119 [hi] SIF v2 प्रारूप ही मान्य है (R-UAE-WPS-2026-07, 2026-07-15)। कृपया उसी में फ़ाइल जमा करें।

## False rejections (genuine)
- G032 [en] ['numbers_not_grounded:21,5'] :: Gratuity is 21 days of basic per year for the first five years. R-UAE-GRAT-2026-01 effective 1 January 2026 clarifies part-time calculation only.
- G069 [en] ['numbers_not_grounded:15000'] :: ₹25,000/- is the current EPF ceiling (R-IN-PF-2026-08, from 1 August 2026). It used to be ₹15,000/-. Thanks for flagging it.
- G071 [en] ['numbers_not_grounded:15000,31'] :: Please use INR 25,000 as the wage ceiling from 1 August 2026, as set by R-IN-PF-2026-08. The superseded figure of INR 15,000 applied up to 31 July 2026 only.
- G072 [en] ['numbers_not_grounded:12'] :: The statutory ceiling rose from INR 15,000 to INR 25,000 effective 1 August 2026 (R-IN-PF-2026-08). The 12% rate is unchanged, so the cap on contribution moves accordingly.
- G075 [en] ['numbers_not_grounded:14'] :: WPS moved from v2 to v3 on 15 July 2026 (R-UAE-WPS-2026-07). v3 has 16 columns, and the establishment reference is now mandatory. Files generated on 14 July 2026 or earlier used the v2 layout with 14 columns.
- G076 [en] ['numbers_not_grounded:14'] :: Rejection E104 means the column count is wrong: the file must have 16 columns (v3) since 15 July 2026 (R-UAE-WPS-2026-07). Your file had 14, which is the old v2 layout.
- G077 [en] ['numbers_not_grounded:14'] :: As per R-UAE-WPS-2026-07, the SIF file is v3 and has 16 columns from 15 Jul 2026. The old 14-column layout no longer works.
- G078 [en] ['numbers_not_grounded:14'] :: The new SIF format (R-UAE-WPS-2026-07, effective 15 July 2026) is v3: 16 columns, new EDR layout. Please upload by 14 August 2026 to avoid delay at the bank.
- G080 [en] ['numbers_not_grounded:21,5'] :: Gratuity is unchanged: R-UAE-GRAT-2026-01 (effective 1 January 2026) only clarifies part-time and fixed-term calculation. The existing rule of 21 days of basic salary per year for the first five years still applies.
- G081 [en] ['numbers_not_grounded:21'] :: The clarification in R-UAE-GRAT-2026-01, effective 1 January 2026, does not change the entitlement. Part-time staff accrue gratuity pro rata on the same 21 day basis.
- G082 [hinglish] ['numbers_not_grounded:15000'] :: EPF ki wage ceiling ab INR 25,000 per month hai, R-IN-PF-2026-08 (1 August 2026 se). Pehle yeh INR 15,000 thi. Payroll mein naya figure use karein.
- G084 [hinglish] ['numbers_not_grounded:15000'] :: Aapke CA ka number sahi hai: ceiling 25k hai (R-IN-PF-2026-08, 1 August 2026). Purani 15k limit hata di gayi hai. Please payroll update kar lein.
- G085 [hinglish] ['numbers_not_grounded:1000'] :: R-IN-PF-2026-08 ke tahat 1 August 2026 se wage ceiling pachchis hazaar rupaye hai, pandrah hazaar nahi. Naye wages par isi ke hisaab se PF banega.
- G086 [hinglish] ['numbers_not_grounded:15000'] :: 1 August 2026 se EPF ceiling ₹25000 hai (R-IN-PF-2026-08). Is se pehle ₹15000 thi, to July tak ke contribution purani limit par hi rahenge.
- G087 [hinglish] ['numbers_not_grounded:14'] :: SIF ab v3 hai: 16 columns, R-UAE-WPS-2026-07 (15 July 2026 se). Purana 14 columns wala v2 layout ab accept nahi hota, isliye E104 aa raha hai.
- G088 [hinglish] ['numbers_not_grounded:14'] :: Aapko 16 columns wali SIF file banani hai, 14 wali nahi (R-UAE-WPS-2026-07, 15 July 2026). Establishment reference ab mandatory hai.
- G089 [hi] ['numbers_not_grounded:15000'] :: EPF की वेतन सीमा अब 25,000 रुपये प्रति माह है (R-IN-PF-2026-08, प्रभावी 2026-08-01)। पहले यह 15,000 रुपये थी। कृपया पेरोल में नई सीमा लागू करें।
- G094 [hi] ['numbers_not_grounded:14'] :: 15 जुलाई 2026 से SIF फ़ाइल v3 में 16 कॉलम की होगी (R-UAE-WPS-2026-07, प्रभावी 2026-07-15)। पुराना 14 कॉलम वाला v2 प्रारूप अब मान्य नहीं है।
- G095 [hi] ['numbers_not_grounded:14'] :: SIF फ़ाइल में सोलह कॉलम होने चाहिए, चौदह नहीं (R-UAE-WPS-2026-07, 2026-07-15)। कृपया फ़ाइल दोबारा बनाएँ।
- G099 [ar] ['numbers_not_grounded:14'] :: الملف الصحيح هو SIF v3 بـ 16 عمودًا (R-UAE-WPS-2026-07، 2026-07-15). الملفات المنشأة في 2026-07-14 أو قبلها كانت بتنسيق v2 (14 عمودًا).
- G101 [en] ['numbers_not_grounded:14,15,15000'] :: Thanks for your message on 14 September 2026. R-IN-PF-2026-08 (effective 1 August 2026) raised the wage ceiling to INR 25,000 per month from INR 15,000. We replied within 15 minutes, and a specialist will follow up by 15 September 2026.
- G102 [en] ['numbers_not_grounded:14'] :: On 14 August 2026 we reviewed your file. Under R-UAE-WPS-2026-07 (effective 15 July 2026) the SIF is v3 with 16 columns, so the 14 columns in your upload will be rejected. Please regenerate it by 15 August 2026.
- G103 [hinglish] ['numbers_not_grounded:14,15'] :: Aapka ticket 14 September ko aaya tha, hum 15 September tak reply denge. R-IN-PF-2026-08 (1 August 2026 se) ke mutabik ceiling 25,000 per month hai, pehle 15,000 thi.
- G104 [en] ['numbers_not_grounded:100402,3'] :: The wage ceiling is INR 25,000 per month (R-IN-PF-2026-08, effective 1 August 2026), replacing INR 15,000. The change is described in version 3 of the circular. Please escalate to PN-100402 if you need an exception.
- G105 [en] ['numbers_not_grounded:14,15'] :: The EPF ceiling is INR 25,000 per month under R-IN-PF-2026-08, effective 1 August 2026. Your ticket was received on 14 September 2026 and we will confirm by 15 September 2026.
- G106 [en] ['numbers_not_grounded:14'] :: Use the 16-column SIF v3 under R-UAE-WPS-2026-07, effective 15 July 2026. Please upload it before 14 August 2026.
- G107 [en] ['numbers_not_grounded:14'] :: The SIF v3 layout (R-UAE-WPS-2026-07) applies from 15 July 2026. We will review your resubmission on 15 August 2026 and reply within 14 days.
- G108 [hinglish] ['numbers_not_grounded:14,15'] :: Ceiling ab INR 25,000 hai (R-IN-PF-2026-08, 1 August 2026 se). Aapka ticket 14 September ko mila, hum 15 September tak update denge.
- G109 [hi] ['numbers_not_grounded:14,15'] :: वेतन सीमा 25,000 रुपये प्रति माह है (R-IN-PF-2026-08, प्रभावी 2026-08-01)। आपका अनुरोध 14 सितंबर को मिला; हम 15 सितंबर तक जवाब देंगे।
- G110 [hi] ['numbers_not_grounded:14'] :: SIF फ़ाइल 16 कॉलम की होनी चाहिए (R-UAE-WPS-2026-07, 2026-07-15)। आपकी फ़ाइल 14 अगस्त को मिली थी।
- G111 [ar] ['numbers_not_grounded:14'] :: يجب أن يتضمن ملف SIF ستة عشر عمودًا (R-UAE-WPS-2026-07، 2026-07-15). استلمنا طلبكم في 14 أغسطس وسنرد في 15 أغسطس.
- G112 [en] ['numbers_not_grounded:12'] :: The ceiling is INR 25,000 per month (R-IN-PF-2026-08, effective 1 August 2026). Contributions stay at 12% of wages up to that ceiling.
- G113 [en] ['numbers_not_grounded:100402'] :: SIF v3 has 16 columns (R-UAE-WPS-2026-07, effective 15 July 2026). If E104 persists, escalate under ticket PN-100402 and quote version 3 of the layout.
- G114 [en] ['numbers_not_grounded:12'] :: Under R-IN-PF-2026-08, effective 1 August 2026, the ceiling is 25k per month, up from 15k. The 12% rate is unchanged.

V2 pre-change PF (PN-100010/100416/100361) + KB-0142 cite: PASS; post-change (PN-100898/100662/100794) without rule: statutory_no_rule_cited; pre-change citing R-IN-PF-2026-08: cited_not_retrieved. Real graph PN-100416, PN-100010, PN-100898, PN-100662: all guard passed, pre cites KB-0142/0143, post cites rule.
V3: seed 20261114 n=30 (pool is 33 statutory stale tickets): final set 30/30; hybrid first-5 only 28/30.
V4: Aadhaar/PAN/IBAN/EID/IN phone caught; UUID and 32+ hex not; 24-char hex not exempt (flagged only if a phone-like 10-digit run is at the boundary).
V5: PN-100402 x3: draft_for_agent, Statutory-Compliance, P2 each time, guard passed, cites R-UAE-WPS-2026-07.
V6: 538 passed, 1 skipped, 2 deselected.