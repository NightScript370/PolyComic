# PolyComic storyboards for review

Two 20-page storyboards (plus an About Me back page) built on one shared page template. **Status: both storyboards were approved, and this document reflects the revisions made after review.**

Source of truth: `app/template/layouts.yaml`, `app/stories/jack.yaml` and `app/stories/red.yaml`. This document was generated from those files. If you change the YAML, regenerate this document.

## Choices made

- **Where planting and climbing fit (Jack).** Page 4 has to be the beanstalk at dawn, so the beans Mother throws out on page 3 grow overnight. **Climbing the beanstalk** is page 5. **Planting the seeds** is page 19: one purple bean lands by the hero child's boot on page 3 (shown in the picture only, not the text), and after the adventure the hero child finds it and plants it with Mother.
- **Gentler endings.** The giant climbs back up and stays safe in the clouds. In Red, nobody is eaten: Grandma hides in the wardrobe and later chases the wolf with a broom, and the woodcutter scares the wolf away with a shout. His axe stays covered and is never swung. Everyone ends the book safe at the table.
- **A whistle for Red.** Mother gives Red a silver whistle on page 1 and Red blows it on page 12, which gives the hero child an active part in the rescue.
- **One trip up the beanstalk.** The classic three trips become one (hen, then harp). On page 20 a golden egg buys Milky-White back.
- **The child's own name and pronouns.** Page text, the subtitle and the About Me heading use only these placeholders: `{name} {they} {them} {their} {theirs} {themself} {They} {Their}`, plus the verb helpers `{is} {was} {has} {does}`. Narration is in the past tense, so no hero pronoun is ever the subject of a present-tense verb. The verb helpers are available, but the current text does not need them. A lower-case *she/he/her/his* written out in the text always means another character (Mother, the giant's wife, Grandma, the wolf or the woodcutter) and is not replaced. Captions and art prompts call the hero "the hero child". Titles stay classic, and the cover subtitle is `starring {name} as Jack` or `starring {name} as Little Red Riding Hood`.
- **What the family looks like.** Each family member (Jack: Mother; Red: Mother and Grandma) has four variants, `fam_a` to `fam_d`, covering a range of skin tones and hair. The pipeline picks one variant id per book and uses it for every family member.
- **No sound-effect or symbol shapes in the art.** Snore puffs, sound rings, musical notes, dizzy stars and shake lines have all been removed and replaced with physical cues (a tipping cup, startled birds, a glowing harp). The global rule against text, letters, symbols and signage is added by the prompt builder, not on each page.
- **Shared slots.** Each slot defines a camera and composition job that both stories fill. No slot uses a pure back view, because the vision check needs the hero's face on every page.
- **Page size.** 3:4 portrait with 3 mm bleed.
- **Dialogue punctuation.** Dialogue is written without quotation marks, to match the fixed page 1–3 text.

## Shared slot template

| Slot | Layout id | Composition | Hero framing | Text zones (preference order) | Jack | Red |
|---|---|---|---|---|---|---|
| 1 | `s01_home_two_shot` | medium two-shot, interior at home | full_face | bottom > top | the empty cupboard - Jack and his mother in the bare kitchen, Milky-White seen through the open door | the basket - Red and her mother at the kitchen table packing the basket for Grandma |
| 2 | `s02_path_encounter` | medium-wide exterior on a road or path | three_quarter | top > bottom | the trade - the old man offers five coloured beans on the road to market | the stranger - the wolf greets Red on the forest path |
| 3 | `s03_dusk_consequence` | wide exterior at dusk or deep shade | three_quarter | bottom > top | out the window - Mother throws the beans into the garden at dusk; Jack watches from the doorway | the shortcut - the wolf slips away down the shady shortcut; Red watches from the path |
| 4 | `s04_wonder_wide` | wide establishing shot of something wonderful revealed by new light | small_in_frame | top > bottom | the beanstalk at dawn - a giant beanstalk climbs into the clouds | the flower meadow - sunlight bursts into a meadow full of flowers |
| 5 | `s05_vertical_ascent` | tall vertical journey | three_quarter | bottom > top | climbing the beanstalk | climbing the winding hill path to Grandma's cottage |
| 6 | `s06_arrival_wide` | wide establishing shot of the destination | small_in_frame | bottom > top | the castle in the clouds | Grandma's cottage on the hill |
| 7 | `s07_threshold_meeting` | medium shot at a large doorway | three_quarter | top > bottom | the giant's wife at the enormous door | the cottage door - a deep voice calls from the bed inside |
| 8 | `s08_interior_secret` | medium-wide interior | three_quarter | top > bottom | hiding in the giant's cupboard | Grandma peeps from the wardrobe and puts a finger to her lips |
| 9 | `s09_low_angle_reveal` | low-angle reveal | profile | bottom > top | Fee-fi-fo-fum - the giant stomps in | What big ears you have |
| 10 | `s10_closeup_reaction` | close-up of the hero's face reacting | full_face | bottom > top | Jack peeks out, wide-eyed | What big eyes you have |
| 11 | `s11_tense_two_shot` | medium-wide two-shot with the big character on one side and the hero on the other | profile | top > bottom | the giant counts his eggs and nods off to sleep | What big teeth you have |
| 12 | `s12_treasure_close` | medium close-up of the hero holding or using a special object or creature, held up near the face so both read clearly | three_quarter | top > bottom | the hen that lays golden eggs | Red blows her silver whistle |
| 13 | `s13_escape_action` | dynamic action across the frame on a diagonal | three_quarter | top > bottom | tiptoeing away with the hen | round and round the table |
| 14 | `s14_second_marvel` | medium two-shot with a second marvel or ally entering the story | three_quarter | top > bottom | the singing harp calls out | Grandma bursts out of the wardrobe with her broom |
| 15 | `s15_chase_action` | outdoor chase | three_quarter | top > bottom | the giant chases Jack across the clouds | out into the garden, the wolf stumbling behind |
| 16 | `s16_vertical_turn` | tall vertical composition | three_quarter | bottom > top | sliding down the beanstalk | the woodcutter appears at the garden gate |
| 17 | `s17_ground_action_two_shot` | medium two-shot on the ground | three_quarter | top > bottom | Chop, chop, chop! - Jack and his mother chop the beanstalk | the woodcutter's great shout sends the wolf running |
| 18 | `s18_aftermath_wide` | wide shot of the big change | small_in_frame | bottom > top | the beanstalk tumbles; the giant stays up in the clouds | the wolf runs far away over the hills; Red hugs Grandma |
| 19 | `s19_garden_planting` | medium close-up outdoors in a garden | three_quarter | top > bottom | Jack plants the last bean | Red plants the meadow flowers in Grandma's garden |
| 20 | `s20_finale_home` | warm wide-medium group shot at a table at home | full_face | bottom > top | home again - a full table, the hen, the harp and Milky-White | tea and cake - everyone safe together |
| back | `about_me` | back-page portrait | full_face | bottom (name, age, favourite things, dedication) | holding the hen | holding the basket |

## Jack and the Beanstalk (the child plays the role as Jack)

**Cover subtitle:** `starring {name} as Jack`. Rendered example: *starring Mia as Jack*

**Hero outfit:** A patched oatmeal-coloured linen tunic with a brown leather belt, moss-green trousers rolled once at the ankle, sturdy brown lace-up boots, and a small faded-blue cloth satchel worn across the body on a long strap.

**Characters:**

- **Mother** (`mother`), pages 1, 3, 16, 17, 18, 19, 20: A slim woman with a kind, tired face, hair in a loose bun under a faded blue headscarf, a long grey-blue dress with a cream apron, sleeves rolled to the elbow.
    - `fam_a`: skin fair, light peach; straight honey-blonde hair in a loose bun
    - `fam_b`: skin light olive; wavy dark-brown hair in a loose bun
    - `fam_c`: skin warm medium brown; curly black hair gathered in a loose bun
    - `fam_d`: skin deep brown; coily black hair gathered in a round puff
- **Milky-White** (`milky_white`), pages 1, 2, 20: A gentle cream-white dairy cow with a pink nose, a soft brown patch around her left eye, short curved horns, and a small brass bell on a rope collar with a long rope lead.
- **the old man** (`old_man`), pages 2: A small, stooped old man with a long white beard, bushy white eyebrows and twinkly eyes, wearing a patched plum-purple cloak with the hood down and leaning on a crooked wooden walking stick.
- **the giant's wife** (`giants_wife`), pages 7, 8: A very tall, kind woman with rosy cheeks and grey hair in a big round bun, wearing a lavender dress and a pink apron with little flowers, holding a huge wooden spoon.
- **the giant** (`giant`), pages 9, 10, 11, 12, 13, 14, 15, 18: An enormous, round-bellied giant with a bushy ginger beard, a bald shiny head, a big round nose and wild eyebrows, wearing a green waistcoat over a mustard shirt and brown breeches. Grumpy and silly rather than frightening; never carries a weapon.
- **the hen** (`hen`), pages 11, 12, 13, 14, 15, 16, 17, 18, 20: A plump little russet-brown hen with a bright red comb, a shiny dark eye and fluffy cream chest feathers. Her eggs are shining gold.
- **the golden harp** (`harp`), pages 14, 15, 16, 17, 18, 20: A small golden lap harp with silver strings; the front pillar is carved into the smiling face of a lady with curly hair. When it sings, its strings shimmer with a soft golden glow.

### Page 1: The empty cupboard

> Template: `{name} and {their} mother had nothing left to sell but Milky-White.`
>
> Example (Mia, they/them): *Mia and their mother had nothing left to sell but Milky-White.*

- **Caption:** The hero child and their mother stand in a bare cottage kitchen beside an open, empty cupboard. Through the open door behind them, Milky-White the cow stands in the yard.
- **Framing:** full face, in layout `s01_home_two_shot`. Insert the hero child standing left of center facing the mother. Full face toward the viewer, front-lit, eyes turned to look at the mother, a small worried frown. Arms at the sides. Wearing hero_outfit. Keep the hero child's head and body in the upper two-thirds of the image.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother, Milky-White
- **Light:** Morning; soft, slightly cool daylight from the doorway; muted browns and greys inside, warm sunlit yard outside.
- **Text must not cover:** hero face, mother face, empty cupboard, Milky-White in the doorway, open door

### Page 2: The trade

> Template: `Five magic beans, said the old man. For one cow.`
>
> Example (Mia, they/them): *Five magic beans, said the old man. For one cow.*

- **Caption:** On a dirt road to market, the old man holds out five coloured beans in his open palm. The hero child holds Milky-White's rope.
- **Framing:** three quarter, in layout `s02_path_encounter`. Insert the hero child standing between Milky-White and the old man, three-quarter view turned toward the old man, holding Milky-White's rope in one hand. Curious, wide-eyed expression, leaning forward slightly to look at the beans. Same clothes as page 1 (hero_outfit).
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the old man, Milky-White
- **Light:** Late morning; bright, clear daylight; pale blue sky, green fields, warm dirt road.
- **Text must not cover:** hero face, old man face, five coloured beans in his palm, Milky-White, the rope

### Page 3: Out the window

> Template: `Beans! she cried. And out the window they went.`
>
> Example (Mia, they/them): *Beans! she cried. And out the window they went.*

- **Caption:** The cottage at dusk. The mother throws the beans from the window and they scatter into the garden below. The hero child stands at the doorway, watching.
- **Framing:** three quarter, in layout `s03_dusk_consequence`. Insert the hero child standing in the open doorway on the right, three-quarter view, looking up and left at the flying beans, mouth open in surprise, one hand on the door frame. Back-lit by the warm doorway light with a soft rim, face still clearly readable. Wearing hero_outfit. One purple bean has landed on the step by the hero child's boot.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother
- **Light:** Dusk; noticeably darker than pages 1-2: deep blue-violet sky with a last orange glow, warm window and doorway light, dark green grass.
- **Text must not cover:** hero face, mother at the window, flying beans, garden below the window

### Page 4: The beanstalk at dawn

> Template: `In the morning, something enormous stood in the garden. A beanstalk, growing up, up, up into the clouds!`
>
> Example (Mia, they/them): *In the morning, something enormous stood in the garden. A beanstalk, growing up, up, up into the clouds!*

- **Caption:** At dawn, a giant beanstalk rises from the garden where the beans fell, twisting up past the cottage roof and into the clouds. The hero child stands small at its foot, looking up.
- **Framing:** small in frame, in layout `s04_wonder_wide`. Insert the hero child small in frame at the lower-left by the base of the beanstalk, head tilted back to look up, face turned so it is still visible, mouth open in amazement, both hands raised. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** none
- **Light:** Dawn; pink and gold light from the right, long soft shadows, fresh greens.
- **Text must not cover:** hero face, beanstalk base, beanstalk rising into clouds, cottage

### Page 5: Climbing the beanstalk

> Template: `{name} took a deep breath and began to climb. Leaf by leaf {they} went, higher than the birds.`
>
> Example (Mia, they/them): *Mia took a deep breath and began to climb. Leaf by leaf they went, higher than the birds.*

- **Caption:** The hero child climbs the beanstalk, high above the cottage. Little birds fly below and the cottage roof is tiny far beneath.
- **Framing:** three quarter, in layout `s05_vertical_ascent`. Insert the hero child climbing the stalk at mid-frame, both hands gripping a leaf stem above, one boot on a leaf below. Three-quarter view looking back over the shoulder toward the viewer with a determined, excited smile. Satchel swinging. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** none
- **Light:** Early morning; clear bright light, the cottage below still misty.
- **Text must not cover:** hero face, beanstalk, tiny cottage far below, birds

### Page 6: The castle in the clouds

> Template: `At the very top, on a road made of clouds, stood the biggest castle {name} had ever seen.`
>
> Example (Mia, they/them): *At the very top, on a road made of clouds, stood the biggest castle Mia had ever seen.*

- **Caption:** Above the clouds, an enormous castle stands at the end of a road of white cloud. The hero child has just stepped off the top of the beanstalk.
- **Framing:** small in frame, in layout `s06_arrival_wide`. Insert the hero child small in the lower-left foreground, just stepped off the beanstalk, three-quarter view turned toward the castle, face visible, eyes wide with wonder. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** none
- **Light:** Midday above the clouds; bright, airy whites and sky blues, warm sunlight on the castle stone.
- **Text must not cover:** hero face, castle, cloud road, beanstalk tip

### Page 7: The giant's wife

> Template: `A giant woman opened the door. You look hungry, she said kindly. Come in, quick!`
>
> Example (Mia, they/them): *A giant woman opened the door. You look hungry, she said kindly. Come in, quick!*

- **Caption:** At the castle's enormous wooden door, the giant's wife bends down and smiles at the hero child, holding out a giant crust of bread.
- **Framing:** three quarter, in layout `s07_threshold_meeting`. Insert the hero child standing at the left of the threshold, three-quarter view turned toward the giant's wife, looking up, a shy hopeful smile, one hand reaching for the bread. Tiny next to her. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the giant's wife
- **Light:** Midday outside, warm amber kitchen light from inside the door.
- **Text must not cover:** hero face, giant's wife face, bread, doorway

### Page 8: Hide!

> Template: `Then the floor began to shake. Hide! she whispered, and {name} slipped into a cupboard, just like the one at home.`
>
> Example (Mia, they/them): *Then the floor began to shake. Hide! she whispered, and Mia slipped into a cupboard, just like the one at home.*

- **Caption:** In the giant's huge kitchen, the giant's wife holds a cupboard door open while the hero child climbs inside. Cups on the table tip and wobble as the floor shakes.
- **Framing:** three quarter, in layout `s08_interior_secret`. Insert the hero child half inside the open cupboard on the right, three-quarter view, looking back toward the viewer with wide eyes, one hand on the cupboard door. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the giant's wife
- **Light:** Interior; warm amber firelight from the left, cosy shadows.
- **Text must not cover:** hero face, giant's wife, open cupboard, giant table and cups

### Page 9: Fee-fi-fo-fum

> Template: `Fee-fi-fo-fum! boomed the giant. I smell somebody new!`
>
> Example (Mia, they/them): *Fee-fi-fo-fum! boomed the giant. I smell somebody new!*

- **Caption:** Seen from below, the giant stomps into the kitchen, sniffing the air with his big round nose. The hero child watches from the crack of the cupboard in the corner.
- **Framing:** profile, in layout `s09_low_angle_reveal`. Insert the hero child small in the lower-left, peeking out through the cupboard crack in profile, looking up at the giant, eyes wide, both hands pressed to the door edge. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the giant
- **Light:** Interior; firelight from below casting warm light up on the giant, cosy rather than menacing.
- **Text must not cover:** hero face, giant face, cupboard

### Page 10: Very, very still

> Template: `{name} peeked through the crack in the door. {They} held {their} breath and stayed very, very still.`
>
> Example (Mia, they/them): *Mia peeked through the crack in the door. They held their breath and stayed very, very still.*

- **Caption:** Close-up of the hero child's face peeking through the narrow gap of the cupboard door, holding their breath. The giant's big blurry boot is in the background.
- **Framing:** full face, in layout `s10_closeup_reaction`. Insert the hero child's face filling the middle of the frame between the cupboard doors, full face toward the viewer, eyes very wide, cheeks puffed from holding their breath, fingers curled on a door edge. Collar of hero_outfit visible.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the giant
- **Light:** Interior; a slice of warm firelight across the hero child's eyes, the rest in soft shadow with a gentle rim light.
- **Text must not cover:** hero face, cupboard door edges, giant's boot

### Page 11: One, two, three golden eggs

> Template: `Wife, bring me my hen! said the giant. He counted her golden eggs, one, two, three, and soon he began to snore.`
>
> Example (Mia, they/them): *Wife, bring me my hen! said the giant. He counted her golden eggs, one, two, three, and soon he began to snore.*

- **Caption:** The giant sits at the huge table with the little hen and three golden eggs before him, his head nodding to sleep. The hero child peeks out from the cupboard on the left.
- **Framing:** profile, in layout `s11_tense_two_shot`. Insert the hero child stepping half out of the cupboard on the left, profile view facing right toward the giant and the hen, a curious, careful expression, one finger raised to their own lips. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the giant, the hen
- **Light:** Interior; firelight lower and softer, evening glow, sleepy mood.
- **Text must not cover:** hero face, giant, hen, three golden eggs

### Page 12: The golden egg

> Template: `The little hen clucked softly. And out came an egg of shining gold!`
>
> Example (Mia, they/them): *The little hen clucked softly. And out came an egg of shining gold!*

- **Caption:** The hero child holds the little brown hen close; a shining golden egg has just appeared in the straw beside her. The giant sleeps in the soft background.
- **Framing:** three quarter, in layout `s12_treasure_close`. Insert the hero child chest-up at center-left, three-quarter view, gently holding the hen against their chest with both arms, looking down at the golden egg with a delighted, astonished smile. The egg's glow lights the face from below. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the hen, the giant
- **Light:** Interior evening; warm golden glow from the egg, firelight behind.
- **Text must not cover:** hero face, hen, golden egg

### Page 13: Tip, tip, tiptoe

> Template: `{name} tucked the hen under {their} arm and tiptoed past the snoring giant. Tip, tip, tiptoe.`
>
> Example (Mia, they/them): *Mia tucked the hen under their arm and tiptoed past the snoring giant. Tip, tip, tiptoe.*

- **Caption:** The hero child tiptoes across the kitchen floor with the hen under one arm. The giant sleeps in his chair with his mouth wide open.
- **Framing:** three quarter, in layout `s13_escape_action`. Insert the hero child center-right, mid-tiptoe, three-quarter view moving right toward the door, glancing back at the giant, eyebrows high, the hen tucked under the left arm. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the giant, the hen
- **Light:** Interior evening; the fire has burned low, warm glow and deeper shadows.
- **Text must not cover:** hero face, hen, sleeping giant, doorway

### Page 14: The singing harp

> Template: `On a shelf by the door, a golden harp began to sing. Master! Master! Wake up!`
>
> Example (Mia, they/them): *On a shelf by the door, a golden harp began to sing. Master! Master! Wake up!*

- **Caption:** By the door, the golden harp sings out, its strings glowing. The hero child, holding the hen, reaches up for it in surprise. Behind them, the giant's eyes are blinking open.
- **Framing:** three quarter, in layout `s14_second_marvel`. Insert the hero child left-center, three-quarter view turned toward the harp, one hand reaching up to take it, the hen still under the other arm, a startled 'uh-oh' expression. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the golden harp, the hen, the giant
- **Light:** Interior; the harp's glow adds a bright gold-white accent against the low firelight.
- **Text must not cover:** hero face, harp, hen, giant waking

### Page 15: Thump, thump, thump!

> Template: `The giant woke with a roar and came thumping after {name}, across the clouds.`
>
> Example (Mia, they/them): *The giant woke with a roar and came thumping after Mia, across the clouds.*

- **Caption:** The hero child runs along the cloud road toward the viewer with the hen and the harp. Far behind, the giant comes thumping out of the castle, slipping on the soft clouds.
- **Framing:** three quarter, in layout `s15_chase_action`. Insert the hero child running toward the viewer in the lower-center foreground, three-quarter view, determined face, the hen under one arm and the harp hugged in the other, satchel flying. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the giant, the hen, the golden harp
- **Light:** Late afternoon above the clouds; warm golden light, long cloud shadows.
- **Text must not cover:** hero face, giant, hen, harp, beanstalk tip

### Page 16: Down, down, down

> Template: `Down, down, down slid {name}, as fast as the wind. The beanstalk swayed and creaked above {them}.`
>
> Example (Mia, they/them): *Down, down, down slid Mia, as fast as the wind. The beanstalk swayed and creaked above them.*

- **Caption:** The hero child slides down the beanstalk toward the cottage, holding on tight. The hen peeks out of the satchel and the harp is tucked under one arm. Below, the mother waves from the garden.
- **Framing:** three quarter, in layout `s16_vertical_turn`. Insert the hero child sliding down the stalk at center, legs wrapped around it, three-quarter view looking down toward the viewer with a brave grin, hair blown upward. The hen peeks from the satchel; the harp is under one arm. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother, the hen, the golden harp
- **Light:** Late afternoon; golden light, the garden below in warm sun.
- **Text must not cover:** hero face, beanstalk, hen, harp, cottage below

### Page 17: Chop, chop, chop!

> Template: `Mother, the axe! cried {name}. And together they chopped, chop, chop, chop!`
>
> Example (Mia, they/them): *Mother, the axe! cried Mia. And together they chopped, chop, chop, chop!*

- **Caption:** In the garden at the foot of the beanstalk, the hero child and the mother hold the axe handle together and chop at the thick stalk. The hen and the harp sit safely on the grass nearby.
- **Framing:** three quarter, in layout `s17_ground_action_two_shot`. Insert the hero child beside the mother, three-quarter view, both hands on the lower part of the axe handle with hers, brave, focused face looking at the stalk. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Mother, the hen, the golden harp
- **Light:** Late afternoon; warm low sun from the left, long shadows.
- **Text must not cover:** hero face, mother face, axe, beanstalk, hen, harp

### Page 18: Whump!

> Template: `The giant scrambled back up to his castle, and the beanstalk came tumbling down. Whump!`
>
> Example (Mia, they/them): *The giant scrambled back up to his castle, and the beanstalk came tumbling down. Whump!*

- **Caption:** The beanstalk topples gently across the fields. High in the clouds, the giant is safe back at the top, shaking his fist. The hero child, the mother, the hen and the harp watch from the garden.
- **Framing:** small in frame, in layout `s18_aftermath_wide`. Insert the hero child small in frame beside the mother in the lower-left, face turned toward the viewer in three-quarter, hands on cheeks, a big relieved grin. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother, the giant, the hen, the golden harp
- **Light:** Sunset; orange and pink sky, warm low light.
- **Text must not cover:** hero face, mother, falling beanstalk, giant in the clouds, hen, harp

### Page 19: Planting the last bean

> Template: `In {their} pocket, {name} found one last bean, the one that had rolled to {their} feet. {They} planted it, and patted the soil, gently, gently.`
>
> Example (Mia, they/them): *In their pocket, Mia found one last bean, the one that had rolled to their feet. They planted it, and patted the soil, gently, gently.*

- **Caption:** Next morning in the garden, the hero child kneels and plants the purple bean in a little hole in the soil while the mother kneels alongside with a watering can.
- **Framing:** three quarter, in layout `s19_garden_planting`. Insert the hero child kneeling center-left, three-quarter view, both hands pressing the soil around the purple bean, face visible above the hands with a proud, gentle smile. A little soil on the fingers. Wearing hero_outfit.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Mother
- **Light:** Fresh morning; cool clear light, dew on the leaves, hopeful.
- **Text must not cover:** hero face, mother, the bean, soil

### Page 20: Home again

> Template: `With one golden egg, they bought Milky-White back. And the cupboard was never empty again.`
>
> Example (Mia, they/them): *With one golden egg, they bought Milky-White back. And the cupboard was never empty again.*

- **Caption:** In the cosy cottage kitchen, the hero child sits at a table full of food with the mother. The cupboard behind is full, the hen sits on the table, the harp plays, and Milky-White pokes her head through the open window.
- **Framing:** full face, in layout `s20_finale_home`. Insert the hero child seated at the center of the table, full face to the viewer, a big happy smile, one hand raised in a wave. Wearing hero_outfit.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother, Milky-White, the hen, the golden harp
- **Light:** Evening; warm lamplight and firelight, the cosiest page in the book; mirrors page 1's room but warm instead of cool.
- **Text must not cover:** hero face, mother face, full cupboard, hen, harp, Milky-White at the window

## Little Red Riding Hood (the child plays the role as Little Red Riding Hood)

**Cover subtitle:** `starring {name} as Little Red Riding Hood`. Rendered example: *starring Mia as Little Red Riding Hood*

**Hero outfit:** A bright red hooded cape fastened at the neck with a round wooden toggle, worn over a cream long-sleeved shirt and a navy-blue knee-length tunic, dark grey leggings and brown ankle boots. From page 2 on, a small silver whistle hangs on a red ribbon around the neck.

**Characters:**

- **Mother** (`mother`), pages 1, 20: A warm, smiling woman with her hair tied back with a green ribbon, wearing a mustard-yellow dress with a white apron and the sleeves pushed up.
    - `fam_a`: skin fair, rosy; curly auburn hair
    - `fam_b`: skin light golden; straight glossy black hair
    - `fam_c`: skin warm medium brown; wavy dark-brown hair
    - `fam_d`: skin deep brown; coily black hair
- **Grandma** (`grandmother`), pages 8, 14, 15, 16, 18, 19, 20: A small, round grandmother with round wire spectacles, rosy cheeks, a long pale-blue nightdress, a lilac knitted cardigan and woolly pink slippers. (Her shawl and nightcap are borrowed by the wolf on pages 7-15.)
    - `fam_a`: skin fair, rosy, softly wrinkled; short curly silver hair
    - `fam_b`: skin light golden, softly wrinkled; straight silver-white hair in a neat bun
    - `fam_c`: skin warm medium brown, softly wrinkled; wavy silver hair in a low bun
    - `fam_d`: skin deep brown, softly wrinkled; short coily silver-white hair
- **the wolf** (`wolf`), pages 2, 3, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18: A tall, lanky grey wolf who walks upright, with shaggy fur, a fluffy tail, big pointed ears, yellow-green eyes and a long snout. Sly and silly rather than frightening; mouth usually closed in a too-polite grin. On pages 7-15 he wears Grandma's frilly white nightcap with a pink ribbon (ears poking through) and her lilac shawl.
- **the woodcutter** (`woodcutter`), pages 16, 17, 18, 20: A tall, broad, friendly woodcutter with a short dark beard and kind eyes, wearing a green felt hat with a feather, a red-and-black checked shirt, a brown leather vest and boots. He carries an axe on his shoulder with its blade covered by a leather sleeve; he never swings it.

### Page 1: The basket

> Template: `Take this basket to Grandma, said Mother, and this little whistle too. And remember, keep to the path!`
>
> Example (Mia, they/them): *Take this basket to Grandma, said Mother, and this little whistle too. And remember, keep to the path!*

- **Caption:** The hero child and the mother in their cottage kitchen. The mother holds out a silver whistle on a red ribbon; the basket sits packed on the table. The forest is visible through the open door.
- **Framing:** full face, in layout `s01_home_two_shot`. Insert the hero child standing left of center facing the mother. Full face toward the viewer, front-lit, eyes turned to look at the mother, an eager smile, one hand reaching for the whistle, the basket on the left arm. Wearing hero_outfit with the hood down. Keep the hero child's head and body in the upper two-thirds of the image.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother
- **Light:** Morning; warm, bright daylight from the door, cosy browns and creams with the red cape as the strongest colour.
- **Text must not cover:** hero face, mother face, whistle, basket, open door to the forest

### Page 2: The stranger

> Template: `Good morning, said a wolf with a very polite smile. And where are you going, little one?`
>
> Example (Mia, they/them): *Good morning, said a wolf with a very polite smile. And where are you going, little one?*

- **Caption:** On the forest path, the wolf steps out from behind a tree and bows. The hero child stands holding the basket, looking up at him.
- **Framing:** three quarter, in layout `s02_path_encounter`. Insert the hero child standing on the path left of center, three-quarter view turned toward the wolf, basket on the left arm, the silver whistle on its ribbon, a curious, slightly unsure expression. Same clothes as page 1 (hero_outfit, hood down).
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the wolf
- **Light:** Late morning; dappled sunlight, fresh greens, pale blue sky through the trees.
- **Text must not cover:** hero face, wolf face, basket, forest path

### Page 3: The shortcut

> Template: `Why not pick some flowers for Grandma? said the wolf. Then off he slipped, down the shady shortcut.`
>
> Example (Mia, they/them): *Why not pick some flowers for Grandma? said the wolf. Then off he slipped, down the shady shortcut.*

- **Caption:** In the deep shade of the forest, the wolf slinks away down a dark side path toward the hill. The hero child stands at the edge of the main path, watching him go.
- **Framing:** three quarter, in layout `s03_dusk_consequence`. Insert the hero child standing at the edge of the main path on the right, three-quarter view, looking up-left after the wolf, a puzzled frown, one hand holding the basket handle tight. A soft rim light on the hair, face still clearly readable. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the wolf
- **Light:** Deep forest shade; noticeably darker than pages 1-2: blue-green shadows, only thin shafts of light.
- **Text must not cover:** hero face, wolf running, shortcut path, distant cottage

### Page 4: The flower meadow

> Template: `{name} stepped out into a meadow full of flowers, all glowing in the sun. Grandma will love these!`
>
> Example (Mia, they/them): *Mia stepped out into a meadow full of flowers, all glowing in the sun. Grandma will love these!*

- **Caption:** Sunlight pours into a wide meadow full of wildflowers. The hero child is small at the lower-left, gathering a bunch of flowers.
- **Framing:** small in frame, in layout `s04_wonder_wide`. Insert the hero child small in frame at the lower-left, kneeling in the flowers with a bunch in one hand, face turned up toward the sunbeams with a delighted smile, the basket alongside. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** none
- **Light:** Bright midday sun bursting in after the shade of page 3; warm gold light, saturated flower colours.
- **Text must not cover:** hero face, flower meadow, sunbeams, basket

### Page 5: Up the hill

> Template: `With {their} basket full of flowers, {name} climbed the winding path, up and up the hill.`
>
> Example (Mia, they/them): *With their basket full of flowers, Mia climbed the winding path, up and up the hill.*

- **Caption:** A winding path zigzags up a steep grassy hill. The hero child climbs it with the basket and flowers; the forest is far below.
- **Framing:** three quarter, in layout `s05_vertical_ascent`. Insert the hero child climbing the middle bend of the path, three-quarter view looking back over the shoulder toward the viewer with a happy, slightly out-of-breath smile, the basket on one arm with flowers poking out, cape swinging. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** none
- **Light:** Early afternoon; clear bright light, long gentle shadows on the hill.
- **Text must not cover:** hero face, winding path, basket with flowers, roof at the top

### Page 6: Grandma's cottage

> Template: `At the top stood Grandma's cottage. But the door was open, just a little.`
>
> Example (Mia, they/them): *At the top stood Grandma's cottage. But the door was open, just a little.*

- **Caption:** Grandma's little thatched cottage on top of the hill, its round green door slightly open. A trail of muddy paw prints leads up the path to the door. The hero child stands at the gate.
- **Framing:** small in frame, in layout `s06_arrival_wide`. Insert the hero child small at the lower-left by the gate, three-quarter view toward the cottage, face visible, head tilted, noticing the paw prints with a thoughtful look. Wearing hero_outfit, hood down, basket on one arm.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** none
- **Light:** Afternoon; the sun slightly lower, a faint cool shadow over the door.
- **Text must not cover:** hero face, cottage, door ajar, paw prints

### Page 7: A very deep voice

> Template: `Come in, dear! called a voice from the bed. It was a very deep voice for Grandma.`
>
> Example (Mia, they/them): *Come in, dear! called a voice from the bed. It was a very deep voice for Grandma.*

- **Caption:** The hero child stands at the open cottage door. Inside, in the bed, a figure lies under the quilt wearing Grandma's frilly nightcap, with two big pointed ears poking out.
- **Framing:** three quarter, in layout `s07_threshold_meeting`. Insert the hero child at the left of the threshold, three-quarter view looking into the room toward the bed, one eyebrow raised, basket on one arm. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the wolf
- **Light:** Interior seen from outside; warm afternoon light at the door, dimmer cosy light inside.
- **Text must not cover:** hero face, bed, nightcap with ears, doorway

### Page 8: Shh!

> Template: `Then {name} saw Grandma, peeping out of the wardrobe! Grandma put a finger to her lips. Shh!`
>
> Example (Mia, they/them): *Then Mia saw Grandma, peeping out of the wardrobe! Grandma put a finger to her lips. Shh!*

- **Caption:** Inside the cottage, Grandma peeps out of the tall flowered wardrobe with a finger to her lips. The hero child, beside the wardrobe, puts the basket on the table. The wolf in the bed on the left has not noticed.
- **Framing:** three quarter, in layout `s08_interior_secret`. Insert the hero child on the right near the wardrobe, three-quarter view, setting the basket on the table, glancing at Grandma with wide, understanding eyes and copying the finger-to-lips gesture. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Grandma, the wolf
- **Light:** Interior; warm afternoon window light from the right, cosy shadow on the bed side.
- **Text must not cover:** hero face, Grandma's face in the wardrobe, wardrobe, wolf in the bed, basket

### Page 9: What big ears

> Template: `Grandma, what big ears you have! said {name}. All the better to hear you with, said the wolf.`
>
> Example (Mia, they/them): *Grandma, what big ears you have! said Mia. All the better to hear you with, said the wolf.*

- **Caption:** Seen from low down, the wolf sits up in the bed in the nightcap, his huge ears poking through it. The hero child stands in the lower corner, looking up.
- **Framing:** profile, in layout `s09_low_angle_reveal`. Insert the hero child in the lower-left corner, profile view facing right and up toward the wolf, hands behind the back, a curious, playing-along expression. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the wolf
- **Light:** Interior; warm lamplight from below, a little comical, not menacing.
- **Text must not cover:** hero face, wolf face, big ears, nightcap

### Page 10: What big eyes

> Template: `Grandma, what big eyes you have! All the better to see you with, my dear.`
>
> Example (Mia, they/them): *Grandma, what big eyes you have! All the better to see you with, my dear.*

- **Caption:** Close-up of the hero child's face, one eyebrow raised, looking sideways. Behind, softly blurred, two big yellow-green eyes peer over the quilt.
- **Framing:** full face, in layout `s10_closeup_reaction`. Insert the hero child's face filling the middle of the frame, full face toward the viewer, eyes glancing sideways toward the big eyes, one eyebrow raised, lips pressed in a knowing little line. Collar of hero_outfit and the whistle ribbon visible.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the wolf
- **Light:** Interior; warm key light on the hero child's face with a soft rim, the background dim.
- **Text must not cover:** hero face, wolf's eyes

### Page 11: What big teeth

> Template: `And Grandma, what big teeth you have! All the better to gobble you up! cried the wolf, and out of bed he sprang.`
>
> Example (Mia, they/them): *And Grandma, what big teeth you have! All the better to gobble you up! cried the wolf, and out of bed he sprang.*

- **Caption:** The wolf springs out of bed, all tangled in the quilt, nightcap slipping, with a big silly grin. The hero child steps back on the left.
- **Framing:** profile, in layout `s11_tense_two_shot`. Insert the hero child on the left, profile facing right toward the wolf, stepping back with one foot, a hand going to the whistle at the neck, a surprised but brave expression. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the wolf
- **Light:** Interior; same warm lamplight, a little more contrast for the action.
- **Text must not cover:** hero face, wolf, quilt, whistle

### Page 12: The silver whistle

> Template: `{name} put the silver whistle to {their} lips and blew with all {their} might. Wheeeeee!`
>
> Example (Mia, they/them): *Mia put the silver whistle to their lips and blew with all their might. Wheeeeee!*

- **Caption:** Close on the hero child blowing the silver whistle, cheeks puffed. Through the open window, startled birds burst up from the windowsill into the sky.
- **Framing:** three quarter, in layout `s12_treasure_close`. Insert the hero child chest-up at center-left, three-quarter view toward the open window, holding the silver whistle to the lips with both hands, cheeks puffed, eyes squeezed with effort. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** none
- **Light:** Interior; bright light from the open window on the face, rest of the room soft.
- **Text must not cover:** hero face, whistle, open window, birds

### Page 13: Round and round

> Template: `Round and round the table ran {name}. The wolf tripped on Grandma's nightdress. Flump!`
>
> Example (Mia, they/them): *Round and round the table ran Mia. The wolf tripped on Grandma's nightdress. Flump!*

- **Caption:** The hero child runs around the little table. Behind, the wolf has tripped and flopped flat on his face, tangled in the nightdress and quilt.
- **Framing:** three quarter, in layout `s13_escape_action`. Insert the hero child mid-run at center-right, three-quarter view moving right, glancing back at the wolf with a half-surprised, half-giggling face, cape flaring. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the wolf
- **Light:** Interior; warm light, playful mood.
- **Text must not cover:** hero face, wolf tangled, table, basket

### Page 14: Grandma's broom

> Template: `Out of the wardrobe burst Grandma, waving her broom. Shoo, you rascal! Shoo!`
>
> Example (Mia, they/them): *Out of the wardrobe burst Grandma, waving her broom. Shoo, you rascal! Shoo!*

- **Caption:** Grandma bursts out of the wardrobe brandishing a broom, spectacles flashing. The hero child stands alongside, cheering. The wolf scrambles toward the door.
- **Framing:** three quarter, in layout `s14_second_marvel`. Insert the hero child left-center, three-quarter view toward Grandma, both fists raised in a cheer, a big delighted smile. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Grandma, the wolf
- **Light:** Interior; a burst of bright light from the open wardrobe and the door.
- **Text must not cover:** hero face, Grandma's face, broom, wolf

### Page 15: Out into the garden

> Template: `{name} and Grandma ran out into the garden, with the wolf stumbling behind them, the nightcap over his eyes.`
>
> Example (Mia, they/them): *Mia and Grandma ran out into the garden, with the wolf stumbling behind them, the nightcap over his eyes.*

- **Caption:** The hero child runs toward the viewer through Grandma's garden, Grandma just behind with her broom. Further back, the wolf stumbles out of the door with the nightcap pulled over his eyes.
- **Framing:** three quarter, in layout `s15_chase_action`. Insert the hero child running toward the viewer in the lower-center, three-quarter view, an excited, determined face, cape streaming. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Grandma, the wolf
- **Light:** Late afternoon; warm golden light, long shadows across the garden.
- **Text must not cover:** hero face, Grandma, wolf, garden path

### Page 16: The woodcutter

> Template: `There at the gate stood the woodcutter, as tall as a tree. I heard your whistle! he said.`
>
> Example (Mia, they/them): *There at the gate stood the woodcutter, as tall as a tree. I heard your whistle! he said.*

- **Caption:** The tall woodcutter stands at the garden gate, his covered axe on his shoulder. The hero child runs to him; Grandma follows behind.
- **Framing:** three quarter, in layout `s16_vertical_turn`. Insert the hero child center-left, three-quarter view, running up and reaching for the woodcutter's hand, a relieved, shining smile. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** the woodcutter, Grandma
- **Light:** Late afternoon; warm low sun behind the woodcutter giving a strong friendly rim light.
- **Text must not cover:** hero face, woodcutter face, gate, Grandma

### Page 17: A great big shout

> Template: `The woodcutter gave a great big shout. The wolf yelped, and ran as fast as his legs could carry him.`
>
> Example (Mia, they/them): *The woodcutter gave a great big shout. The wolf yelped, and ran as fast as his legs could carry him.*

- **Caption:** The hero child holds the woodcutter's hand as he gives a mighty shout. The wolf dashes away down the hill, the nightcap flying off his head.
- **Framing:** three quarter, in layout `s17_ground_action_two_shot`. Insert the hero child beside the woodcutter, three-quarter view, one hand holding his, the other pointing after the wolf, a brave, happy face. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** the woodcutter, the wolf
- **Light:** Late afternoon; warm low sun from the left, long shadows down the hill.
- **Text must not cover:** hero face, woodcutter, wolf running away, nightcap

### Page 18: Far, far away

> Template: `Over the hills and far away ran the wolf, and he never came back. {name} gave Grandma the biggest hug.`
>
> Example (Mia, they/them): *Over the hills and far away ran the wolf, and he never came back. Mia gave Grandma the biggest hug.*

- **Caption:** Wide view of the hills at sunset. The wolf is a tiny figure running over the far hills. By the cottage gate, the hero child hugs Grandma while the woodcutter smiles.
- **Framing:** small in frame, in layout `s18_aftermath_wide`. Insert the hero child small in frame, hugging Grandma in the lower-left, face turned toward the viewer in three-quarter, eyes closed in a happy smile. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Grandma, the woodcutter, the wolf
- **Light:** Sunset; orange and pink sky, warm low light.
- **Text must not cover:** hero face, Grandma, woodcutter, wolf far away, cottage gate

### Page 19: Planting the meadow flowers

> Template: `Next morning, {name} and Grandma planted the meadow flowers by the door, so the garden would bloom all summer long.`
>
> Example (Mia, they/them): *Next morning, Mia and Grandma planted the meadow flowers by the door, so the garden would bloom all summer long.*

- **Caption:** In Grandma's garden in the morning, the hero child kneels and plants the wildflowers from the meadow in the soil by the green door. Grandma kneels alongside with a little trowel.
- **Framing:** three quarter, in layout `s19_garden_planting`. Insert the hero child kneeling center-left, three-quarter view, both hands patting the soil around a planted flower, face visible above the hands with a proud, gentle smile. Wearing hero_outfit, hood down.
- **Text zone:** top (fallback: bottom)
- **Supporting characters:** Grandma
- **Light:** Fresh morning; cool, clear light, dew on the petals, hopeful.
- **Text must not cover:** hero face, Grandma, flowers, soil

### Page 20: Tea and cake

> Template: `Then Mother came up the hill, and everyone sat down to tea and cake. Safe and sound, together.`
>
> Example (Mia, they/them): *Then Mother came up the hill, and everyone sat down to tea and cake. Safe and sound, together.*

- **Caption:** Inside Grandma's cosy cottage, the hero child sits at the center of the table with the mother, Grandma and the woodcutter, sharing tea and cake. The meadow flowers stand in a jug.
- **Framing:** full face, in layout `s20_finale_home`. Insert the hero child seated at the center of the table, full face to the viewer, a big happy smile, holding up a slice of cake. Wearing hero_outfit, hood down.
- **Text zone:** bottom (fallback: top)
- **Supporting characters:** Mother, Grandma, the woodcutter
- **Light:** Evening; warm lamplight and firelight, the cosiest page in the book.
- **Text must not cover:** hero face, mother face, Grandma face, woodcutter face, cake, flowers
