(*
  Import séquentiel AAF / OMF / vidéos dans Pro Tools
  ====================================================
  Pro Tools n'a pas de dictionnaire AppleScript : ce script pilote l'interface
  via System Events (GUI scripting). Prérequis :
   - autoriser l'app qui lance le script (Éditeur de script, Script Menu, etc.)
     dans Réglages Système > Confidentialité et sécurité > Accessibilité ;
   - session ouverte, fenêtre Edit au premier plan, compteur principal en Timecode ;
   - libellés ci-dessous conformes à ta version / langue de Pro Tools.
  Pour calibrer : ouvre à la main le dialogue "Import Session Data" (ou
  "Video Import Options"), puis lance le script en mode "Diagnostic" :
  il écrit l'inventaire des éléments d'interface sur le Bureau.
  Ne touche ni clavier ni souris pendant l'exécution.

  Modes de placement :
   1. TC saisi pour chaque fichier (valeur proposée = précédent + espacement)
   2. Espacement fixe entre points d'entrée (ex. toutes les 10 min)
   3. Bout à bout + blanc : durée du média lue via Spotlight (mdls) ;
      si inconnue (AAF, disque non indexé), la durée est demandée.
*)

-- ==================== RÉGLAGES ====================
property fps : 25 -- base images de la session (24 pour 23.976 NDF, 30 pour 29.97 NDF). Drop-frame non géré.
property fpsRealString : "25" -- cadence réelle pour convertir les durées (ex. "23.976"), en texte avec un point
property defaultStartTC : "01:00:00:00"
property defaultStep : "00:10:00:00" -- espacement entre points d'entrée (modes 1 et 2)
property defaultGap : "00:00:10:00" -- blanc entre deux médias (mode 3)

property procName : "Pro Tools"
property menuFileName : "File"
property menuImportName : "Import"
property itemSessionDataPrefix : "Session Data"
property itemVideoPrefix : "Video"
property winImportSessionName : "Import Session Data"
property winVideoOptionsName : "Video Import Options"
property tcMapPrefix : "Map start timecode" -- élément du menu Timecode Mapping Options
property mainVideoTrackLabel : "Main Video Track" -- destination à choisir dans la 1re fenêtre d'import vidéo
property spotLabel : "Spot" -- emplacement à choisir dans le menu déroulant de la 1re fenêtre
property spotWindowKeyword : "Spot" -- mot contenu dans le titre de la 2e fenêtre (saisie du TC)
property timeScaleItem : "Timecode" -- échelle de temps à utiliser dans la fenêtre Spot
property timeScaleValues : {"Bars|Beats", "Min:Sec", "Timecode", "Feet+Frames", "Samples"}
property editWindowPrefix : "Edit:"

property uiDelay : 0.6
property dialogTimeout : 30 -- s pour l'apparition d'un dialogue
property importTimeout : 1800 -- s max par import (copie / conversion de médias)

property aafExtensions : {"aaf", "omf", "omfi"}
property videoExtensions : {"mov", "mp4", "m4v", "mxf", "avi"}
property keypadCodes : {82, 83, 84, 85, 86, 87, 88, 89, 91, 92} -- touches 0 à 9 du pavé numérique

-- ==================== PROGRAMME ====================
on run
	set modeList to {"1 — TC saisi pour chaque fichier", "2 — Espacement fixe entre points d'entrée", "3 — Bout à bout + blanc (durée du média)", "Diagnostic — inventaire de la fenêtre Pro Tools au premier plan"}
	set modeChoice to choose from list modeList with prompt "Mode de placement :" default items {item 2 of modeList}
	if modeChoice is false then return
	set modeChoice to item 1 of modeChoice
	if modeChoice starts with "Diagnostic" then
		dumpFrontWindow()
		return
	end if
	set modeNum to (character 1 of modeChoice) as integer
	
	set theFiles to choose file with prompt "Sélectionne les AAF / OMF / vidéos à importer" with multiple selections allowed
	set posixList to sortedPosixPaths(theFiles)
	
	set orderText to ""
	repeat with i from 1 to count of posixList
		set orderText to orderText & i & ". " & baseName(item i of posixList) & return
	end repeat
	display dialog "Ordre d'import (tri alphabétique) :" & return & return & orderText buttons {"Annuler", "Continuer"} default button 2 cancel button 1
	
	set currentFrames to tcToFrames(askTC("Point d'entrée du premier fichier :", defaultStartTC))
	set stepFrames to 0
	set gapFrames to 0
	if modeNum is 1 or modeNum is 2 then set stepFrames to tcToFrames(askTC("Espacement entre deux points d'entrée :", defaultStep))
	if modeNum is 3 then set gapFrames to tcToFrames(askTC("Blanc entre deux médias :", defaultGap))
	
	set report to ""
	repeat with i from 1 to count of posixList
		set p to item i of posixList
		set ext to fileExtension(p)
		if modeNum is 1 and i > 1 then
			set currentFrames to tcToFrames(askTC("Point d'entrée pour " & baseName(p) & " :", framesToTC(currentFrames)))
		end if
		set tc to framesToTC(currentFrames)
		
		try
			if aafExtensions contains ext then
				importSessionData(p, tc)
			else if videoExtensions contains ext then
				importVideo(p, tc)
			else
				error "Extension non gérée : " & ext
			end if
			set report to report & "✓ " & baseName(p) & " @ " & tc & return
		on error errMsg number errNum
			if errNum is -128 then error number -128
			set report to report & "✗ " & baseName(p) & " : " & errMsg & return
			set r to button returned of (display dialog "Échec sur " & baseName(p) & " :" & return & errMsg buttons {"Arrêter", "Passer au suivant"} default button 2)
			if r is "Arrêter" then exit repeat
		end try
		
		-- point d'entrée suivant
		if modeNum is 3 then
			set d to mediaDurationFrames(p)
			if d is 0 then set d to tcToFrames(askTC("Durée inconnue pour " & baseName(p) & ". Durée à réserver :", defaultStep))
			set currentFrames to currentFrames + d + gapFrames
		else
			set currentFrames to currentFrames + stepFrames
		end if
	end repeat
	
	activate
	display dialog "Import terminé :" & return & return & report buttons {"OK"} default button 1
end run

-- ==================== IMPORTS ====================
on importSessionData(p, tc)
	focusProTools()
	setCursorTC(tc)
	clickImportMenuItem(itemSessionDataPrefix)
	chooseFileInOpenPanel(p)
	waitForWindow(winImportSessionName, dialogTimeout)
	
	tell application "System Events" to tell process procName
		set w to window winImportSessionName
		
		-- Timecode Mapping Options -> "Map start timecode to:"
		set mapped to false
		repeat with pb in (my allOfRole(w, "AXPopUpButton"))
			try
				if (value of pb as text) contains "timecode" then
					click pb
					delay uiDelay
					click (first menu item of menu 1 of pb whose name starts with tcMapPrefix)
					set mapped to true
					exit repeat
				end if
			end try
		end repeat
		if not mapped then error "Option « " & tcMapPrefix & " » introuvable (lance le mode Diagnostic)"
		delay uiDelay
		
		-- Champ timecode de destination (À CALIBRER si besoin : index du champ)
		set tcFields to my allOfRole(w, "AXTextField")
		if (count of tcFields) = 0 then error "Champ timecode introuvable (lance le mode Diagnostic)"
		set tcField to item 1 of tcFields
		try
			set value of tcField to tc
		on error
			click tcField
			delay 0.2
			keystroke "a" using command down
			keystroke my stripTC(tc)
		end try
		delay uiDelay
		my clickButtonNamed(w, "OK")
	end tell
	
	waitUntilIdle(importTimeout)
end importSessionData

on importVideo(p, tc)
	focusProTools()
	clickImportMenuItem(itemVideoPrefix)
	chooseFileInOpenPanel(p)
	waitForWindow(winVideoOptionsName, dialogTimeout)
	
	-- Fenêtre 1 : destination "Main Video Track" + emplacement "Spot", puis Entrée.
	-- Si une option n'est pas trouvée, on valide quand même (réglages par défaut).
	tell application "System Events" to tell process procName
		set w to window winVideoOptionsName
		set mainDone to false
		set spotDone to false
		repeat with pb in (my allOfRole(w, "AXPopUpButton"))
			if mainDone and spotDone then exit repeat
			try
				set v to value of pb as text
				if v is mainVideoTrackLabel then
					set mainDone to true
				else if v is spotLabel then
					set spotDone to true
				else
					click pb
					delay uiDelay
					set picked to false
					if not mainDone then
						set hits to (every menu item of menu 1 of pb whose name is mainVideoTrackLabel)
						if (count of hits) > 0 then
							click item 1 of hits
							set mainDone to true
							set picked to true
						end if
					end if
					if (not picked) and (not spotDone) then
						set hits to (every menu item of menu 1 of pb whose name is spotLabel)
						if (count of hits) > 0 then
							click item 1 of hits
							set spotDone to true
							set picked to true
						end if
					end if
					if not picked then
						key code 53 -- Échap : referme ce menu sans rien changer
						delay 0.2
					end if
				end if
			end try
		end repeat
		-- "Main Video Track" peut aussi être un bouton radio ou une case
		if not mainDone then
			repeat with r in ((my allOfRole(w, "AXRadioButton")) & (my allOfRole(w, "AXCheckBox")))
				try
					if (name of r as text) contains mainVideoTrackLabel then
						if (role of r) is "AXCheckBox" then
							if (value of r) is 0 then click r
						else
							click r
						end if
						exit repeat
					end if
				end try
			end repeat
		end if
		delay uiDelay
		key code 36 -- Entrée : valide la fenêtre
	end tell
	
	-- Fenêtre 2 : saisie du TC de départ
	set swName to waitForWindowContaining(spotWindowKeyword, dialogTimeout)
	tell application "System Events" to tell process procName
		set sw to window swName
		-- échelle de temps en Timecode
		repeat with pb in (my allOfRole(sw, "AXPopUpButton"))
			try
				set v to value of pb as text
				if timeScaleValues contains v then
					if v is not timeScaleItem then
						click pb
						delay uiDelay
						click menu item timeScaleItem of menu 1 of pb
						delay uiDelay
					end if
					exit repeat
				end if
			end try
		end repeat
		-- champ Start = 1er champ texte (sinon : saisie directe, le champ Start a le focus à l'ouverture)
		set tcFields to my allOfRole(sw, "AXTextField")
		if (count of tcFields) > 0 then
			click item 1 of tcFields
			delay 0.2
			keystroke "a" using command down
		end if
		my typeDigits(my stripTC(tc))
		delay uiDelay
		my clickButtonNamed(sw, "OK")
	end tell
	
	waitUntilIdle(importTimeout)
end importVideo

-- ==================== PILOTAGE PRO TOOLS ====================
on focusProTools()
	tell application procName to activate
	delay uiDelay
end focusProTools

-- Place le curseur via le compteur principal ("=" du pavé numérique, saisie, Entrée)
on setCursorTC(tc)
	set digitString to stripTC(tc)
	tell application "System Events" to tell process procName
		key code 81
		delay 0.3
		repeat with c in (characters of digitString)
			key code (item (((c as text) as integer) + 1) of keypadCodes)
			delay 0.03
		end repeat
		key code 76
	end tell
	delay uiDelay
end setCursorTC

on clickImportMenuItem(prefix)
	tell application "System Events" to tell process procName
		set importMenu to menu 1 of menu item menuImportName of menu 1 of menu bar item menuFileName of menu bar 1
		repeat with mi in (every menu item of importMenu)
			try
				if (name of mi) starts with prefix then
					click mi
					return
				end if
			end try
		end repeat
	end tell
	error "Menu « " & menuFileName & " > " & menuImportName & " > " & prefix & " » introuvable"
end clickImportMenuItem

-- Sélectionne un fichier dans le panneau d'ouverture via Cmd+Maj+G
on chooseFileInOpenPanel(p)
	delay 1
	set oldClip to missing value
	try
		set oldClip to the clipboard
	end try
	set the clipboard to p
	tell application "System Events" to tell process procName
		keystroke "g" using {command down, shift down}
		delay 0.8
		keystroke "a" using command down
		keystroke "v" using command down
		delay 0.5
		key code 36 -- aller au fichier
		delay 1
		key code 36 -- Ouvrir
	end tell
	delay 1
	if oldClip is not missing value then
		try
			set the clipboard to oldClip
		end try
	end if
end chooseFileInOpenPanel

on waitForWindow(wName, timeoutSec)
	set t0 to current date
	repeat
		tell application "System Events" to tell process procName
			if exists window wName then return
		end tell
		if ((current date) - t0) > timeoutSec then error "Fenêtre « " & wName & " » non apparue"
		delay 0.3
	end repeat
end waitForWindow

-- Attend le retour de la fenêtre Edit au premier plan (fin d'import / conversion)
on waitUntilIdle(timeoutSec)
	set t0 to current date
	delay 1
	repeat
		set frontName to ""
		try
			tell application "System Events" to tell process procName
				set frontName to (name of window 1) as text
			end tell
		end try
		if frontName starts with editWindowPrefix then return
		if ((current date) - t0) > timeoutSec then error "Pro Tools n'est pas revenu à la fenêtre Edit (dialogue en attente ?)"
		delay 0.5
	end repeat
end waitUntilIdle

on dumpFrontWindow()
	tell application procName to activate
	delay 1
	tell application "System Events" to tell process procName
		set w to window 1
		set out to "Fenêtre : " & my safeText(name of w) & linefeed
		repeat with e in (entire contents of w)
			try
				set out to out & my safeText(role of e) & " | name=" & my safeText(name of e) & " | value=" & my safeText(value of e) & linefeed
			end try
		end repeat
	end tell
	set f to (POSIX path of (path to desktop)) & "protools_ui_dump.txt"
	do shell script "printf %s " & quoted form of out & " > " & quoted form of f
	display dialog "Inventaire écrit dans " & f buttons {"OK"} default button 1
end dumpFrontWindow

-- Tous les éléments d'un rôle donné, y compris dans les groupes imbriqués
on allOfRole(w, roleName)
	set found to {}
	tell application "System Events"
		repeat with e in (entire contents of w)
			try
				if (role of e) is roleName then set end of found to (contents of e)
			end try
		end repeat
	end tell
	return found
end allOfRole

on clickButtonNamed(w, btnName)
	tell application "System Events"
		repeat with b in (my allOfRole(w, "AXButton"))
			try
				if (name of b as text) is btnName then
					click b
					return
				end if
			end try
		end repeat
	end tell
	error "Bouton « " & btnName & " » introuvable"
end clickButtonNamed

-- Saisie de chiffres via le pavé numérique (indépendant de la disposition AZERTY/QWERTY)
on typeDigits(digitString)
	tell application "System Events"
		repeat with c in (characters of digitString)
			key code (item (((c as text) as integer) + 1) of keypadCodes)
			delay 0.03
		end repeat
	end tell
end typeDigits

on waitForWindowContaining(keyword, timeoutSec)
	set t0 to current date
	repeat
		try
			tell application "System Events" to tell process procName
				repeat with wn in (name of every window)
					if (wn as text) contains keyword then return (wn as text)
				end repeat
			end tell
		end try
		if ((current date) - t0) > timeoutSec then error "Aucune fenêtre contenant « " & keyword & " » n'est apparue"
		delay 0.3
	end repeat
end waitForWindowContaining

-- ==================== UTILITAIRES ====================
on tcToFrames(tc)
	set oldTID to AppleScript's text item delimiters
	set AppleScript's text item delimiters to {":", ";", "."}
	set parts to text items of tc
	set AppleScript's text item delimiters to oldTID
	if (count of parts) is not 4 then error "Timecode invalide : " & tc
	set h to (item 1 of parts) as integer
	set m to (item 2 of parts) as integer
	set s to (item 3 of parts) as integer
	set f to (item 4 of parts) as integer
	if m > 59 or s > 59 or f ≥ fps or h < 0 then error "Timecode invalide : " & tc
	return ((h * 3600 + m * 60 + s) * fps) + f
end tcToFrames

on framesToTC(n)
	set f to n mod fps
	set totalS to n div fps
	return pad2(totalS div 3600) & ":" & pad2((totalS div 60) mod 60) & ":" & pad2(totalS mod 60) & ":" & pad2(f)
end framesToTC

on askTC(promptText, defaultTC)
	repeat
		set r to text returned of (display dialog promptText & return & "(HH:MM:SS:FF, base " & fps & " i/s)" default answer defaultTC)
		try
			return framesToTC(tcToFrames(r))
		on error
			display dialog "Timecode invalide : " & r buttons {"OK"} default button 1
		end try
	end repeat
end askTC

on mediaDurationFrames(p)
	set cmd to "mdls -raw -name kMDItemDurationSeconds " & quoted form of p & " | awk -v f=" & fpsRealString & " '{ if ($1 ~ /^[0-9.]+$/) printf \"%d\", ($1*f)+0.999; else print 0 }'"
	try
		return (do shell script cmd) as integer
	on error
		return 0
	end try
end mediaDurationFrames

on sortedPosixPaths(aliasList)
	set s to ""
	repeat with a in aliasList
		set s to s & (POSIX path of a) & linefeed
	end repeat
	return paragraphs of (do shell script "printf %s " & quoted form of s & " | sort -f")
end sortedPosixPaths

on baseName(p)
	set oldTID to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "/"
	set b to last text item of p
	set AppleScript's text item delimiters to oldTID
	return b
end baseName

on fileExtension(p)
	set b to baseName(p)
	if b does not contain "." then return ""
	set oldTID to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "."
	set e to last text item of b
	set AppleScript's text item delimiters to oldTID
	return e
end fileExtension

on stripTC(tc)
	return do shell script "printf %s " & quoted form of tc & " | tr -cd '0-9'"
end stripTC

on pad2(x)
	set t to "0" & (x as text)
	return text -2 thru -1 of t
end pad2

on safeText(x)
	try
		if x is missing value then return "-"
		return x as text
	on error
		return "?"
	end try
end safeText
