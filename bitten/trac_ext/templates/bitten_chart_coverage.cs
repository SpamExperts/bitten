<chart>
 <chart_type>
  <value>area</value>
  <value>area</value>
 </chart_type>
 <axis_category size='10'
   skip='<?cs var:len(chart.data) / 6 ?>' orientation='vertical' />
 <axis_ticks value_ticks='false' category_ticks='true' major_thickness='1'
   minor_thickness='0' major_color='000000' position='outside' />
 
 <chart_data>
  <row><string /><?cs
   each:item = chart.data ?><string><?cs var:item.rev ?></string><?cs
   /each ?></row><row><string>Lines of Code</string><?cs
   each:item = chart.data ?><string><?cs var:item.loc ?></string><?cs
   /each ?></row><row><string>Coverage</string><?cs
   each:item = chart.data ?><string><?cs var:item.cov ?></string><?cs
   /each ?></row>
 </chart_data>
 
 <chart_grid_h alpha='5' color='333333' thickness='2'/>
 <chart_pref line_thickness='2' point_shape='none'/> 
 <legend_rect x='-100' y='-100' width='10' height='10'/>

 <draw>
  <text width="320" height="40" h_align="center" v_align="bottom" size="12" ><?cs
    var:chart.title ?></text>
 </draw>
 
 <series_color>
  <color>bbbbbb</color>
  <color>99ff99</color>
 </series_color>

</chart>